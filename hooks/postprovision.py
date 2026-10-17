"""Post-provision hook (v3): certs, Entra app, skills, agents, staged publish, Bot Service, Teams.

After Bicep deploys Azure resources, this hook:
0. Uploads SF JWT Bearer cert to Key Vault + creates APIM cert binding (if cert exists locally)
1. Creates Chat App Entra app registration (az CLI — delegated permissions)
2. Ensures the Salesforce OAuth2 identity-passthrough connection
3. Updates Chat App Container App env vars
4. Updates OBO APIM Named Values
5. Uploads skills/<name> as Foundry skill versions (new version only on change)
6. Creates salesforce-assistant and customer360-assistant versions (only when changed);
   customer360 uses direct SF+SN MCP tools or one toolbox (C360_TOOLS_MODE)
7. Per agent: Agent Application + immutable '<agent>-v<N>' deployment, pre-route
   smoke test of the exact version, route 100%, assert (previous route recorded
   for `python hooks/postprovision.py --rollback`)
8. Per agent: Bot Service bootstrap / adoption (Bicep manages it afterwards)
9. Per agent: Teams app in the org catalog via Graph (non-fatal)

Steps 5-8 are fatal on failure. Uses az CLI for Entra ops because the Graph Bicep
extension requires Application.ReadWrite.All on the ARM deployment identity, which
is not available in managed tenants.
"""

# FLEET_PATCH_V1
# Auto-applied by fleet-preflight.py to make direct Python invocation work
# outside the azd hook context (for the finalize-customer360 step).
def _fleet_bootstrap_env():
    import os, subprocess
    if os.environ.get("AZURE_ENV_NAME"):
        return
    try:
        out = subprocess.run(["azd", "env", "get-values"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=30, check=False)
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if not line or "=" not in line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass
_fleet_bootstrap_env()

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import uuid


def run(cmd: str, parse_json: bool = False):
    """Run a shell command and return stdout (or parsed JSON)."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, shell=True,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if not out:
        return None
    if parse_json:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None
    return out


def azd_env_set(key: str, value: str):
    """Set an azd environment variable."""
    subprocess.run(
        f'azd env set {key} "{value}"',
        shell=True, capture_output=True, text=True,
    )
    os.environ[key] = value
    print(f"  azd env set {key}={value[:20]}{'...' if len(value) > 20 else ''}")


def _write_temp_json(data):
    """Write data as JSON to a temp file and return the file path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def _graph_patch(object_id: str, body: dict):
    """PATCH a Microsoft Graph application resource."""
    body_file = _write_temp_json(body)
    try:
        return run(
            f'az rest --method PATCH '
            f'--url "https://graph.microsoft.com/v1.0/applications/{object_id}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
    finally:
        os.unlink(body_file)


def upload_cert_and_configure_apim():
    """Upload SF JWT Bearer cert to Key Vault and create APIM cert binding.

    On first deploy the cert isn't in KV yet (Bicep skips the cert module when
    SF_JWT_BEARER_CERT_THUMBPRINT is empty). This function:
    1. Checks for local cert at certs/sf-jwt-bearer.pfx
    2. Assigns deployer Key Vault Certificates Officer role (idempotent)
    3. Imports cert into Key Vault (with retry for RBAC propagation)
    4. Reads thumbprint and persists via azd env set
    5. Creates APIM cert binding via ARM REST
    6. Updates APIM Named Value SfJwtBearerCertThumbprint
    """
    cert_path = os.path.join(os.getcwd(), "certs", "sf-jwt-bearer.pfx")
    if not os.path.exists(cert_path):
        print("  No local cert found at certs/sf-jwt-bearer.pfx — skipping")
        print("  Generate with: openssl req -x509 -nodes -days 365 ...")
        print("  See docs/installation.md for details")
        return

    kv_name = os.environ.get("KEY_VAULT_NAME", "")
    if not kv_name:
        print("  WARNING: KEY_VAULT_NAME not set — skipping cert upload")
        return

    cert_name = os.environ.get("SF_JWT_BEARER_CERT_NAME", "sf-jwt-bearer")

    # Check if cert already exists in KV
    thumbprint = run(
        f'az keyvault certificate show --vault-name {kv_name} '
        f'--name {cert_name} --query x509ThumbprintHex -o tsv'
    )

    if thumbprint:
        print(f"  Certificate already in Key Vault (thumbprint: {thumbprint})")
    else:
        # Assign deployer Key Vault Certificates Officer role
        deployer_oid = run('az ad signed-in-user show --only-show-errors || az ad sp show --id "$(az account show --query user.name -o tsv)" --query id -o tsv')
        if not deployer_oid:
            print("  WARNING: Could not get deployer OID — skipping cert upload")
            return

        sub_id = run("az account show --query id -o tsv")
        rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
        kv_resource_id = (
            f"/subscriptions/{sub_id}/resourceGroups/{rg}"
            f"/providers/Microsoft.KeyVault/vaults/{kv_name}"
        )
        role_def_id = "a4417e6f-fecd-4de8-b567-7b0420556985"  # Key Vault Certificates Officer
        # Deterministic assignment name for idempotency
        assignment_name = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{kv_resource_id}/{deployer_oid}/{role_def_id}"))

        role_url = (
            f"https://management.azure.com{kv_resource_id}"
            f"/providers/Microsoft.Authorization/roleAssignments/{assignment_name}"
            f"?api-version=2022-04-01"
        )
        role_body = {
            "properties": {
                "roleDefinitionId": f"/subscriptions/{sub_id}/providers/Microsoft.Authorization/roleDefinitions/{role_def_id}",
                "principalId": deployer_oid,
                "principalType": "User",
            }
        }
        body_file = _write_temp_json(role_body)
        try:
            print(f"  Assigning Key Vault Certificates Officer to deployer...")
            run(
                f'az rest --method PUT --url "{role_url}" '
                f'--headers "Content-Type=application/json" '
                f'--body "@{body_file}"',
                parse_json=True,
            )
        finally:
            os.unlink(body_file)

        # Import cert with retry (RBAC propagation can take ~30s)
        max_retries = 6
        retry_delay = 10
        for attempt in range(max_retries):
            result = run(
                f'az keyvault certificate import --vault-name {kv_name} '
                f'--name {cert_name} --file "{cert_path}" --password ""'
            )
            if result is not None:
                print(f"  Certificate imported to Key Vault")
                break
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: RBAC not yet propagated, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            else:
                print("  ERROR: Failed to import certificate after retries")
                return

        # Read thumbprint
        thumbprint = run(
            f'az keyvault certificate show --vault-name {kv_name} '
            f'--name {cert_name} --query x509ThumbprintHex -o tsv'
        )
        if not thumbprint:
            print("  ERROR: Could not read certificate thumbprint from Key Vault")
            return

    # Persist thumbprint for future azd up runs
    azd_env_set("SF_JWT_BEARER_CERT_THUMBPRINT", thumbprint)

    # Create APIM cert binding via ARM REST
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    apim_name = os.environ.get("APIM_NAME", "")
    kv_uri = f"https://{kv_name}.vault.azure.net/"

    if not apim_name:
        print("  WARNING: APIM_NAME not set — skipping APIM cert binding")
        return

    cert_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}"
        f"/certificates/{cert_name}"
        f"?api-version=2024-06-01-preview"
    )
    cert_body = {
        "properties": {
            "keyVault": {
                "secretIdentifier": f"{kv_uri}secrets/{cert_name}",
            }
        }
    }
    body_file = _write_temp_json(cert_body)
    try:
        print(f"  Creating APIM certificate binding '{cert_name}'...")
        result = run(
            f'az rest --method PUT --url "{cert_url}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
        if result:
            print("  APIM certificate binding created")
        else:
            print("  WARNING: Failed to create APIM certificate binding")
    finally:
        os.unlink(body_file)

    # Update APIM Named Value for thumbprint
    nv_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}"
        f"/namedValues/SfJwtBearerCertThumbprint"
        f"?api-version=2024-06-01-preview"
    )
    nv_body = {
        "properties": {
            "displayName": "SfJwtBearerCertThumbprint",
            "value": thumbprint,
            "secret": False,
        }
    }
    body_file = _write_temp_json(nv_body)
    try:
        print(f"  Updating APIM Named Value 'SfJwtBearerCertThumbprint'...")
        result = run(
            f'az rest --method PUT --url "{nv_url}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
        if result:
            print(f"  SfJwtBearerCertThumbprint = {thumbprint}")
        else:
            print("  WARNING: Failed to update SfJwtBearerCertThumbprint Named Value")
    finally:
        os.unlink(body_file)


def create_chat_app_entra_registration():
    """Create Entra app registration for the Chat App SPA (MSAL.js).

    Creates (idempotent — skips if app exists by displayName):
    - SPA app registration with redirect URIs for localhost + deployed FQDN
    - Service principal
    - Sets CHAT_APP_ENTRA_CLIENT_ID via azd env set
    """
    env_name = os.environ.get("AZURE_ENV_NAME", "default")
    display_name = f"Chat App ({env_name})"

    # Check if already exists
    app_id = run(
        f"az ad app list --filter \"displayName eq '{display_name}'\" "
        "--query \"[0].appId\" -o tsv"
    )

    if app_id:
        print(f"  Already exists: {app_id}")
    else:
        app_id = run(
            f'az ad app create --display-name "{display_name}" '
            "--sign-in-audience AzureADMyOrg "
            "--is-fallback-public-client true "
            "--query appId -o tsv"
        )
        if not app_id:
            print("  ERROR: Failed to create Chat App Entra registration")
            return
        print(f"  Created: {app_id}")

    # Configure SPA redirect URIs
    chat_app_fqdn = os.environ.get("CHAT_APP_FQDN", "")
    redirect_uris = ["http://localhost:8080"]
    if chat_app_fqdn:
        redirect_uris.append(f"https://{chat_app_fqdn}")

    obj_id = run(f'az ad app show --id "{app_id}" --query id -o tsv')
    _graph_patch(obj_id, {
        "spa": {"redirectUris": redirect_uris}
    })
    print(f"  SPA redirect URIs: {redirect_uris}")

    # Declare required resource access for Azure AI Services (https://ai.azure.com)
    # Without this, Entra rejects token requests for https://ai.azure.com/.default
    _graph_patch(obj_id, {
        "requiredResourceAccess": [
            {
                "resourceAppId": "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe",  # Azure AI (ai.azure.com)
                "resourceAccess": [
                    {
                        "id": "1a7925b5-f871-417a-9b8b-303f9f29fa10",  # user_impersonation
                        "type": "Scope",
                    }
                ],
            }
        ]
    })
    print("  Required resource access: Azure AI Services (user_impersonation)")

    # Ensure service principal
    sp_id = run(f'az ad sp show --id "{app_id}" --query id -o tsv')
    if not sp_id:
        sp_id = run(f'az ad sp create --id "{app_id}" --query id -o tsv')
        print(f"  SP created: {sp_id}")
    else:
        print(f"  SP exists: {sp_id}")

    azd_env_set("CHAT_APP_ENTRA_CLIENT_ID", app_id)


def update_chat_app_settings():
    """Update chat Container App with Entra client ID and tenant ID.

    These env vars are needed by the chat app's /api/config endpoint
    to serve MSAL configuration to the browser.
    """
    chat_app_name = os.environ.get("CHAT_APP_CONTAINER_APP_NAME", "ca-chat-app")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    client_id = os.environ.get("CHAT_APP_ENTRA_CLIENT_ID", "")
    tenant_id = run("az account show --query tenantId -o tsv")

    if not client_id or not tenant_id or not rg:
        print("  WARNING: Missing env vars — skipping chat app settings update")
        return

    agent_name = "salesforce-assistant"
    log_ws_id = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID", "")
    bot_msa_app_id = os.environ.get("AGENT_BOT_MSA_APP_ID", "")

    print(f"  Updating {chat_app_name} environment variables...")
    env_vars = (
        f'"CHAT_APP_ENTRA_CLIENT_ID={client_id}" '
        f'"TENANT_ID={tenant_id}" '
        f'"AGENT_NAME={agent_name}"'
    )
    if log_ws_id:
        env_vars += f' "LOG_ANALYTICS_WORKSPACE_ID={log_ws_id}"'
    if bot_msa_app_id:
        env_vars += f' "AGENT_BOT_MSA_APP_ID={bot_msa_app_id}"'
    result = run(
        f'az containerapp update --name {chat_app_name} --resource-group {rg} '
        f'--set-env-vars {env_vars}',
    )
    if result is not None:
        print("  Container App env vars updated")
    else:
        print("  WARNING: Failed to update Container App env vars")


def update_obo_apim_named_values():
    """Update APIM Named Values for OBO JWT Bearer flow.

    Sets SfOboClientId and SfOboLoginUrl from environment variables.
    Bicep deploys placeholders; this patches them with real values.
    """
    sf_obo_client_id = os.environ.get("SF_CONNECTED_APP_CLIENT_ID", "")
    sf_instance_url = os.environ.get("SF_INSTANCE_URL", "")

    if not sf_obo_client_id:
        print("  Skipping — SF_CONNECTED_APP_CLIENT_ID not set")
        print("  Set it with: azd env set SF_CONNECTED_APP_CLIENT_ID <obo-eca-consumer-key>")
        return

    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    apim_name = os.environ.get("APIM_NAME", "")

    if not sub_id:
        print("  WARNING: Could not get subscription ID — skipping")
        return

    named_values = {
        "SfOboClientId": sf_obo_client_id,
    }
    if sf_instance_url:
        named_values["SfOboLoginUrl"] = sf_instance_url

    for nv_name, nv_value in named_values.items():
        url = (
            f"https://management.azure.com/subscriptions/{sub_id}"
            f"/resourceGroups/{rg}"
            f"/providers/Microsoft.ApiManagement/service/{apim_name}"
            f"/namedValues/{nv_name}"
            f"?api-version=2024-06-01-preview"
        )
        body = {
            "properties": {
                "displayName": nv_name,
                "value": nv_value,
                "secret": False,
            }
        }
        body_file = _write_temp_json(body)
        try:
            print(f"  Updating APIM Named Value '{nv_name}' = {nv_value[:40]}...")
            result = run(
                f'az rest --method PUT --url "{url}" '
                f'--headers "Content-Type=application/json" '
                f'--body "@{body_file}"',
                parse_json=True,
            )
            if result:
                print(f"  {nv_name} updated successfully")
            else:
                print(f"  WARNING: Failed to update {nv_name}")
        finally:
            os.unlink(body_file)


def _register_connection_redirect(oauth_client_id, redirect_url):
    """Append an ApiHub connection's redirect URI to the OAuth app's web.redirectUris.

    Each OAuth2 ApiHub connection gets its own redirect URI. If it is not
    registered on the app, interactive consent fails with AADSTS50011.
    These connections hold a client secret (confidential client), so the
    redirect belongs in web.redirectUris — NOT publicClient.
    Returns True on success.
    """
    app = run(f'az ad app show --id {oauth_client_id}', parse_json=True)
    if not app or not isinstance(app, dict):
        return False
    uris = list((app.get("web") or {}).get("redirectUris") or [])
    alt = redirect_url.replace(
        "https://global.consent.azure-apim.net", "https://consent.azure-apim.net"
    )
    to_add = [u for u in (redirect_url, alt) if u and u not in uris]
    if not to_add:
        print("  Redirect URI already registered on the OAuth app")
        return True
    body_file = _write_temp_json({"web": {"redirectUris": uris + to_add}})
    try:
        result = run(
            f'az rest --method PATCH '
            f'--url "https://graph.microsoft.com/v1.0/applications/{app["id"]}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
        )
        print(f"  Registered redirect URI(s) on app {oauth_client_id}: {to_add}")
        return True
    except Exception as e:
        print(f"  WARNING: Could not register redirect URI automatically: {e}")
        return False
    finally:
        os.unlink(body_file)


def ensure_obo_connection():
    """Ensure the SF OBO connection exists as OAuth2 identity passthrough. NEVER
    deletes or overwrites an existing connection.

    HISTORY (do not regress): Foundry blocks Microsoft-audience tokens to
    self-hosted MCP endpoints ("Cannot pass Microsoft token to untrusted MCP
    endpoint"). The trust list is Microsoft-managed with no customer opt-in, so
    authType UserEntraToken is dead for custom MCP servers — OAuth2 identity
    passthrough backed by an Entra app registration is the only supported
    per-user pattern. A previous version of this function deleted and recreated
    the connection as UserEntraToken on every azd up, silently reverting live
    fixes and taking down every agent (2026-06-24 outage).
    """
    connection_name = os.environ.get("SF_OBO_CONNECTION_NAME", "salesforce-obo-oauth2")
    sf_mcp_obo_endpoint = os.environ.get("APIM_SF_MCP_OBO_ENDPOINT", "")

    if not sf_mcp_obo_endpoint:
        apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
        if apim_gateway:
            sf_mcp_obo_endpoint = f"{apim_gateway}/salesforce-mcp-obo/mcp"
    if not sf_mcp_obo_endpoint:
        print("  WARNING: No SF MCP OBO endpoint — skipping connection check")
        return

    sub_id = run("az account show --query id -o tsv")
    if not sub_id:
        print("  WARNING: Could not get subscription ID")
        return

    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    account = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")

    url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        f"/projects/{project}/connections/{connection_name}"
        f"?api-version=2025-04-01-preview"
    )

    existing = run(f'az rest --method GET --url "{url}"', parse_json=True)
    if existing and isinstance(existing, dict):
        auth_type = (existing.get("properties") or {}).get("authType", "")
        if auth_type == "OAuth2":
            print(f"  Connection '{connection_name}' exists (OAuth2) — leaving untouched")
        else:
            print(f"  WARNING: Connection '{connection_name}' exists with authType "
                  f"'{auth_type}' (expected OAuth2).")
            print("  NOT modifying it automatically. Delete it and re-run, or fix it "
                  "in the Foundry portal (agents wired to a non-OAuth2 connection "
                  "fail with the 'untrusted MCP endpoint' error).")
        return

    oauth_client_id = os.environ.get("MCP_OAUTH_CLIENT_ID", "")
    oauth_client_secret = os.environ.get("MCP_OAUTH_CLIENT_SECRET", "")
    if not oauth_client_id or not oauth_client_secret:
        print(f"  Connection '{connection_name}' does not exist and "
              "MCP_OAUTH_CLIENT_ID / MCP_OAUTH_CLIENT_SECRET are not set.")
        print("  Create an Entra app registration exposing scope "
              "api://<appId>/access_as_user, then:")
        print("    azd env set MCP_OAUTH_CLIENT_ID <appId>")
        print("    azd env set MCP_OAUTH_CLIENT_SECRET <secret>")
        print("  and re-run this hook. NOT creating a UserEntraToken fallback — "
              "Foundry rejects it for custom MCP endpoints.")
        return

    tenant_id = run("az account show --query tenantId -o tsv")
    login = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0"
    body = {
        "properties": {
            "authType": "OAuth2",
            "category": "RemoteTool",
            "group": "GenericProtocol",
            "target": sf_mcp_obo_endpoint,
            "authorizationUrl": f"{login}/authorize",
            "tokenUrl": f"{login}/token",
            "refreshUrl": f"{login}/token",
            "scopes": ["offline_access", f"api://{oauth_client_id}/access_as_user"],
            "credentials": {
                "clientId": oauth_client_id,
                "clientSecret": oauth_client_secret,
            },
            "metadata": {"type": "custom_MCP"},
            "isSharedToAll": False,
        }
    }

    body_file = _write_temp_json(body)
    try:
        print(f"  Creating OAuth2 connection '{connection_name}'...")
        result = run(
            f'az rest --method PUT --url "{url}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
        if not result:
            print("  WARNING: Failed to create SF OBO connection")
            return
        print("  SF OBO connection created (OAuth2)")
    finally:
        os.unlink(body_file)

    # Register the connection's redirect URI on the OAuth app (AADSTS50011 otherwise).
    created = run(f'az rest --method GET --url "{url}"', parse_json=True)
    redirect_url = ""
    if created and isinstance(created, dict):
        redirect_url = (created.get("properties") or {}).get("redirectUrl", "") or ""
    if redirect_url:
        if not _register_connection_redirect(oauth_client_id, redirect_url):
            print(f"  ACTION REQUIRED: add '{redirect_url}' (and its "
                  "consent.azure-apim.net variant) to web.redirectUris of app "
                  f"{oauth_client_id}, or user consent will fail with AADSTS50011.")
    else:
        print("  WARNING: Could not read the connection's redirectUrl — verify the "
              f"redirect URI of '{connection_name}' is registered on app "
              f"{oauth_client_id} before users consent.")


# Lean system prompts (v3). Domain know-how lives in skills/<name>/SKILL.md and is
# attached to the agents as Foundry skills; keep only role and hard rules here.
SALESFORCE_AGENT_INSTRUCTIONS = """\
You are a Salesforce assistant acting on behalf of the signed-in user.
- Use your skills for how to query and update Salesforce.
- Tell the user your plan before calling tools.
- Always fetch fresh data with tools; never answer record questions from earlier turns alone.
- Confirm with the user before any create, update, upsert, delete or approval action.
- Summarize results in plain language; never dump raw JSON.
"""

CUSTOMER360_AGENT_INSTRUCTIONS = """\
You are a Customer 360 assistant over Salesforce (CRM) and ServiceNow (ITSM), acting on \
behalf of the signed-in user.
- Use your skills for how to query each system, correlate them, and for revenue-at-risk, \
meeting-prep and change-risk requests.
- Tell the user what you will query in each system before calling tools.
- Always fetch fresh data from both systems; never answer record questions from earlier turns alone.
- Confirm with the user before any write in either system.
- Explain what matched when you correlate records; never invent links.
- Lead with monetary amounts; summarize in plain language, never raw JSON.
"""


# ---------------------------------------------------------------------------
# v3: skills, agents, toolbox
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(REPO_ROOT, "skills")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "gpt-5.4")

SF_TOOLS = ["whoami", "list_objects", "describe_object", "soql_query",
            "search_records", "write_record", "process_approval"]
SN_TOOLS = ["discover", "query", "write"]

SF_AGENT_SKILLS = ["salesforce-crm"]
C360_AGENT_SKILLS = ["salesforce-crm", "servicenow-itsm", "customer360-correlation",
                     "revenue-at-risk", "meeting-prep", "change-risk"]

C360_STARTERS = [
    "Give me a Customer 360 view for Contoso Ltd",
    "Acme Corp reports API gateway outages \u2014 what's the full picture?",
    "Prepare me for my call with Fabrikam Inc tomorrow",
    "Which strategic accounts have the most open incidents? Show revenue at risk",
    "Show me unresolved P1 incidents and correlated Salesforce cases",
    "P1/P2 incidents with affected Salesforce accounts \u2014 rank by revenue at risk",
]

ARM_APPS_API = "2026-01-15-preview"


def _project_client():
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    endpoint = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("Missing AI_FOUNDRY_PROJECT_ENDPOINT")
    return AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential(),
                           allow_preview=True)


def _with_propagation_retry(fn, what):
    """After a fresh deploy the Foundry data plane takes 5-15 min; 'not found' is transient."""
    delay = 10
    for attempt in range(6):
        try:
            return fn()
        except Exception as e:
            if "not found" in str(e).lower() and attempt < 5:
                print(f"  {what}: attempt {attempt + 1}/6 failed ({e}); retrying in {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                raise


def _is_not_found(exc):
    from azure.core.exceptions import ResourceNotFoundError
    return isinstance(exc, ResourceNotFoundError) or getattr(exc, "status_code", None) == 404


def _fingerprint(obj):
    import hashlib
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _sf_mcp_endpoint():
    endpoint = os.environ.get("APIM_SF_MCP_OBO_ENDPOINT", "")
    gateway = os.environ.get("APIM_GATEWAY_URL", "")
    return endpoint or (f"{gateway}/salesforce-mcp-obo/mcp" if gateway else "")


def _sn_mcp_endpoint():
    gateway = os.environ.get("APIM_GATEWAY_URL", "")
    return f"{gateway}/servicenow-mcp-obo/mcp" if gateway else ""


# --- Skills ----------------------------------------------------------------

def _local_skill_files(name):
    """Files of skills/<name>, text normalized to LF so uploads are platform-independent."""
    root = os.path.join(SKILLS_DIR, name)
    files = {}
    for dirpath, _, filenames in os.walk(root):
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, "rb") as f:
                data = f.read()
            if fn.endswith((".md", ".txt", ".json", ".yaml", ".yml")):
                data = data.replace(b"\r\n", b"\n")
            files[rel] = data
    return files


def _zip_files(files):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(files):
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[rel])
    return buf.getvalue()


def _unzip(data):
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


def local_skill_names():
    return sorted(d for d in os.listdir(SKILLS_DIR)
                  if os.path.isfile(os.path.join(SKILLS_DIR, d, "SKILL.md")))


def ensure_skills(project_client):
    """Upload skills/<name> as Foundry skill versions; new version only when content changed.

    Returns {skill_name: exact_version}.
    """
    from azure.ai.projects.models import CreateSkillVersionFromFilesBody
    versions = {}
    for name in local_skill_names():
        local = _local_skill_files(name)
        latest = None
        try:
            latest = project_client.beta.skills.get(name).latest_version
        except Exception as e:
            if not _is_not_found(e):
                raise
        if latest:
            remote = _unzip(b"".join(project_client.beta.skills.download_version(name, latest)))
            if remote == local:
                versions[name] = latest
                print(f"  skill {name}: unchanged (v{latest})")
                continue
        created = project_client.beta.skills.create_from_files(
            name,
            content=CreateSkillVersionFromFilesBody(
                files=[(f"{name}.zip", _zip_files(local))], default=True),
        )
        versions[name] = created.version
        print(f"  skill {name}: uploaded v{created.version}")
    return versions


def compose_instructions(base, skill_names, versions):
    """Lean base prompt + the exact Foundry skill versions this agent uses.

    Prompt agents cannot load skills at runtime yet: PromptAgentDefinition.skills
    needs the GitHub Copilot harness (preview, not enabled on every subscription)
    and toolbox skills are MCP *resources*, which the Responses MCP tool does not
    read. Inlining keeps one versioned source of truth until runtime loading works.
    """
    parts = [base.rstrip(), "## Skills",
             "Each skill below is a versioned Foundry skill. Follow the one that "
             "matches the request."]
    for name in skill_names:
        body = _local_skill_files(name)["SKILL.md"].decode("utf-8").strip()
        parts.append(f'<skill name="{name}" version="{versions[name]}">\n{body}\n</skill>')
    return "\n\n".join(parts) + "\n"


def _skill_settings(base_instructions, skill_names, versions):
    """Definition kwargs for SKILLS_MODE=inline (default) or native (needs the harness)."""
    mode = os.environ.get("SKILLS_MODE", "inline").lower()
    if mode == "native":
        from azure.ai.projects.models import GitHubCopilotHarness, SkillReference
        return {"instructions": base_instructions,
                "skills": [SkillReference(name=n, version=versions[n]) for n in skill_names],
                "harness": GitHubCopilotHarness()}
    return {"instructions": compose_instructions(base_instructions, skill_names, versions)}


def _latest_agent_version(project_client, agent_name):
    try:
        return project_client.agents.get(agent_name).as_dict()["versions"]["latest"]
    except Exception as e:
        if not _is_not_found(e):
            raise
        return None


def _create_latest_version(project_client, agent_name, definition, metadata):
    """create_version that always yields a NEW latest version.

    Foundry dedupes identical definition+metadata to the existing version, which
    would leave 'latest' (what agent_reference callers such as the web chat get)
    on another definition. A publish timestamp keeps every create distinct.
    """
    metadata = {**metadata, "published_at": str(int(time.time()))}
    av = _with_propagation_retry(
        lambda: project_client.agents.create_version(
            agent_name=agent_name, definition=definition, metadata=metadata),
        f"create {agent_name}")
    latest = _latest_agent_version(project_client, agent_name)
    if not latest or str(latest["version"]) != str(av.version):
        raise RuntimeError(f"{agent_name}: created v{av.version} but latest is "
                           f"v{latest and latest['version']}")
    return av.version


def _ensure_agent_version(project_client, agent_name, definition, metadata=None):
    """Create a new agent version only when the definition changed.

    Returns (version, created).
    """
    metadata = dict(metadata or {})
    fp = _fingerprint({"definition": definition.as_dict(), "metadata": metadata})
    latest = _latest_agent_version(project_client, agent_name)
    if latest and (latest.get("metadata") or {}).get("v3_fingerprint") == fp:
        print(f"  {agent_name}: definition unchanged (v{latest['version']})")
        return latest["version"], False
    version = _create_latest_version(project_client, agent_name, definition,
                                     {**metadata, "v3_fingerprint": fp})
    print(f"  {agent_name}: created v{version}")
    return version, True


def _restore_as_latest(project_client, agent_name, version):
    """Republish an earlier version's definition as the newest version."""
    old = project_client.agents.get_version(agent_name, str(version))
    meta = {k: v for k, v in (old.metadata or {}).items() if k != "published_at"}
    restored = _create_latest_version(project_client, agent_name, old.definition,
                                      {**meta, "restored_from": str(version)})
    print(f"  {agent_name}: v{version} republished as v{restored} (latest)")
    return restored


def _connection_url(name):
    return f"{_arm_project_base()}/connections/{name}?api-version=2025-04-01-preview"


def _require_oauth2_connection(name):
    conn = run(f'az rest --method GET --url "{_connection_url(name)}"', parse_json=True)
    if not conn or not isinstance(conn, dict):
        raise RuntimeError(
            f"Connection '{name}' not found in the Foundry project. Create it as an OAuth2 "
            "identity-passthrough connection (see ensure_obo_connection) or deploy "
            "snow-meta-tool first.")
    auth_type = (conn.get("properties") or {}).get("authType", "")
    if auth_type != "OAuth2":
        raise RuntimeError(
            f"Connection '{name}' has authType '{auth_type}' (expected OAuth2); agents wired "
            "to it fail with 'Cannot pass Microsoft token to untrusted MCP endpoint'.")


def create_agent(project_client, skill_versions):
    """salesforce-assistant: SF MCP tool (OAuth2 OBO connection) + salesforce-crm skill."""
    from azure.ai.projects.models import MCPTool, PromptAgentDefinition

    sf_url = _sf_mcp_endpoint()
    if not sf_url:
        raise RuntimeError("No SF MCP endpoint (APIM_SF_MCP_OBO_ENDPOINT / APIM_GATEWAY_URL)")
    connection = os.environ.get("SF_OBO_CONNECTION_NAME", "salesforce-obo-oauth2")
    print(f"  SF MCP: {sf_url} (connection {connection})")

    definition = PromptAgentDefinition(
        model=AGENT_MODEL,
        tools=[MCPTool(server_label="salesforce_mcp", server_url=sf_url,
                       project_connection_id=connection, require_approval="never",
                       allowed_tools=SF_TOOLS)],
        **_skill_settings(SALESFORCE_AGENT_INSTRUCTIONS, SF_AGENT_SKILLS, skill_versions),
    )
    version, _ = _ensure_agent_version(
        project_client, "salesforce-assistant", definition,
        metadata={"skills": ",".join(f"{n}@{skill_versions[n]}" for n in SF_AGENT_SKILLS)})
    return version


def ensure_customer360_toolbox(project_client, skill_versions, sf_url, sf_conn, sn_url, sn_conn):
    """One toolbox: SF + SN MCP servers (each on its OAuth2 OBO connection) + all skills.

    Returns the version-pinned toolbox MCP URL.
    """
    from azure.ai.projects.models import MCPToolboxTool, ToolboxSkillReference

    name = os.environ.get("C360_TOOLBOX_NAME", "customer360-toolbox")
    tools = [
        MCPToolboxTool(server_label="salesforce_mcp", server_url=sf_url,
                       project_connection_id=sf_conn, require_approval="never",
                       allowed_tools=SF_TOOLS),
        MCPToolboxTool(server_label="servicenow_mcp", server_url=sn_url,
                       project_connection_id=sn_conn, require_approval="never",
                       allowed_tools=SN_TOOLS),
    ]
    skills = [ToolboxSkillReference(name=n, version=skill_versions[n]) for n in C360_AGENT_SKILLS]
    fp = _fingerprint({"tools": [t.as_dict() for t in tools],
                       "skills": [s.as_dict() for s in skills]})
    version = None
    try:
        latest = project_client.toolboxes.get(name).as_dict()["versions"]["latest"]
        if (latest.get("metadata") or {}).get("v3_fingerprint") == fp:
            version = latest["version"]
            print(f"  toolbox {name}: unchanged (v{version})")
    except Exception as e:
        if not _is_not_found(e):
            raise
    if version is None:
        tb = project_client.toolboxes.create_version(
            name=name, tools=tools, skills=skills, metadata={"v3_fingerprint": fp},
            description="Customer 360: Salesforce + ServiceNow MCP (OAuth2 OBO) + skills")
        version = tb.version
        print(f"  toolbox {name}: created v{version}")
    endpoint = os.environ["AI_FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    return f"{endpoint}/toolboxes/{name}/versions/{version}/mcp?api-version=v1"


def create_customer360_agent(project_client, skill_versions):
    """customer360-assistant over SF + SN.

    C360_TOOLS_MODE=direct (default): one MCP tool per system.
    C360_TOOLS_MODE=toolbox: one MCP tool pointing at the version-pinned toolbox.
    Returns the agent version, or None when the SN connection is missing.
    """
    from azure.ai.projects.models import MCPTool, PromptAgentDefinition

    sf_url, sn_url = _sf_mcp_endpoint(), _sn_mcp_endpoint()
    if not sf_url or not sn_url:
        raise RuntimeError("No SF/SN MCP endpoint (APIM_GATEWAY_URL missing)")
    sf_conn = os.environ.get("SF_OBO_CONNECTION_NAME", "salesforce-obo-oauth2")
    sn_conn = os.environ.get("SN_OBO_CONNECTION_NAME", "servicenow-obo-oauth2")
    try:
        _require_oauth2_connection(sn_conn)
    except RuntimeError as e:
        print(f"WARNING: {e}\n  Skipping Customer 360 agent.")
        return None

    mode = os.environ.get("C360_TOOLS_MODE", "direct").lower()
    if mode == "toolbox":
        toolbox_url = ensure_customer360_toolbox(
            project_client, skill_versions, sf_url, sf_conn, sn_url, sn_conn)
        # No project_connection_id: Foundry authenticates the agent to its own project's
        # toolbox (spike: no embedded token; OAuth consent still surfaces per system).
        tools = [MCPTool(server_label="c360_toolbox", server_url=toolbox_url,
                         require_approval="never")]
        print(f"  tools: toolbox {toolbox_url}")
    else:
        tools = [
            MCPTool(server_label="salesforce_mcp", server_url=sf_url,
                    project_connection_id=sf_conn, require_approval="never",
                    allowed_tools=SF_TOOLS),
            MCPTool(server_label="servicenow_mcp", server_url=sn_url,
                    project_connection_id=sn_conn, require_approval="never",
                    allowed_tools=SN_TOOLS),
        ]
        print(f"  tools: direct SF ({sf_conn}) + SN ({sn_conn})")

    definition = PromptAgentDefinition(
        model=AGENT_MODEL, tools=tools,
        **_skill_settings(CUSTOMER360_AGENT_INSTRUCTIONS, C360_AGENT_SKILLS, skill_versions),
    )
    # Foundry UI reads starters from definition.conversation_starters and metadata.starterPrompts.
    definition["conversation_starters"] = [{"text": s} for s in C360_STARTERS]
    version, _ = _ensure_agent_version(
        project_client, "customer360-assistant", definition,
        metadata={"starterPrompts": "\n".join(C360_STARTERS), "tools_mode": mode,
                  "skills": ",".join(f"{n}@{skill_versions[n]}" for n in C360_AGENT_SKILLS)})
    return version


# ---------------------------------------------------------------------------
# Agent Applications, deployments, staged publish (ARM control plane)
# ---------------------------------------------------------------------------

def _arm_rest(method, url, body=None, parse_json_response=True):
    """Call Azure ARM REST API via az rest. Returns parsed JSON or None."""
    cmd = f'az rest --method {method} --url "{url}"'
    if body is not None:
        body_file = _write_temp_json(body)
        cmd += f' --headers "Content-Type=application/json" --body "@{body_file}"'
    else:
        body_file = None
    try:
        return run(cmd, parse_json=parse_json_response)
    finally:
        if body_file:
            os.unlink(body_file)


def _arm_project_base():
    """ARM control-plane base URL for the Foundry project."""
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    account = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")
    if not (sub_id and rg and account and project):
        raise RuntimeError("Missing subscription / AZURE_RESOURCE_GROUP / COGNITIVE_ACCOUNT_NAME "
                           "/ AI_FOUNDRY_PROJECT_NAME")
    return (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        f"/projects/{project}"
    )


def _poll_provisioning(get_url, timeout=300, interval=10):
    """Poll a GET URL until provisioningState is Succeeded/Failed or timeout."""
    elapsed = 0
    while elapsed < timeout:
        result = _arm_rest("GET", get_url)
        if result and isinstance(result, dict):
            state = result.get("properties", {}).get("provisioningState", "")
            if state in ("Succeeded", "Failed", "Canceled"):
                return result
            print(f"  Provisioning state: {state} (waiting...)")
        time.sleep(interval)
        elapsed += interval
    print(f"  WARNING: Polling timed out after {timeout}s")
    return None


# Per-agent publish settings. env_prefix keys the persisted azd env vars.
PUBLISHED_AGENTS = [
    {
        "agent": "salesforce-assistant", "app": "salesforce-assistant",
        "display": "Salesforce Assistant", "env_prefix": "SF",
        "msa_env": "AGENT_BOT_MSA_APP_ID", "bot_env": "AGENT_BOT_NAME", "bot_prefix": "agent-bot",
        "desc_short": "AI assistant with Salesforce access",
        "desc_full": "AI-powered assistant that can query and update Salesforce on your "
                     "behalf using natural language.",
    },
    {
        "agent": "customer360-assistant", "app": "customer360-assistant",
        "display": "Customer 360 Assistant", "env_prefix": "C360",
        "msa_env": "C360_BOT_MSA_APP_ID", "bot_env": "C360_BOT_NAME", "bot_prefix": "c360-bot",
        "desc_short": "Customer 360 across Salesforce and ServiceNow",
        "desc_full": "AI assistant that correlates Salesforce CRM and ServiceNow ITSM data "
                     "on your behalf: account view, revenue at risk, meeting prep and "
                     "change risk.",
    },
]


def _app_url(app_name):
    return f"{_arm_project_base()}/applications/{app_name}?api-version={ARM_APPS_API}"


def _deployments_url(app_name, deployment_name=None):
    suffix = f"/{deployment_name}" if deployment_name else ""
    return (f"{_arm_project_base()}/applications/{app_name}/agentDeployments{suffix}"
            f"?api-version={ARM_APPS_API}")


def ensure_agent_application(cfg):
    """Create or reconcile the Agent Application; persist its identity for the Bot Service.

    Returns the Foundry-managed identity clientId (Bot Service msaAppId).
    """
    url = _app_url(cfg["app"])
    existing = _arm_rest("GET", url)
    props = (existing or {}).get("properties", {}) if isinstance(existing, dict) else {}
    client_id = props.get("defaultInstanceIdentity", {}).get("clientId")
    agents = [a.get("agentName") for a in props.get("agents", [])]
    if client_id and agents == [cfg["agent"]]:
        print(f"  Application '{cfg['app']}' exists (clientId {client_id})")
    else:
        body = {"properties": {
            "displayName": cfg["display"],
            "agents": [{"agentName": cfg["agent"]}],
            "authorizationPolicy": {"authorizationScheme": "Channels"},
        }}
        rules = [{k: r[k] for k in ("ruleId", "deploymentId", "trafficPercentage") if k in r}
                 for r in (props.get("trafficRoutingPolicy") or {}).get("rules") or []
                 if r.get("deploymentId")]
        if rules:
            body["properties"]["trafficRoutingPolicy"] = {"protocol": "FixedRatio", "rules": rules}
        verb = "Reconciling" if existing else "Creating"
        print(f"  {verb} Application '{cfg['app']}' (agents {agents} -> [{cfg['agent']}])...")
        result = _arm_rest("PUT", url, body)
        if not result:
            raise RuntimeError(f"Failed to create Agent Application '{cfg['app']}'")
        if result.get("properties", {}).get("provisioningState") != "Succeeded":
            result = _poll_provisioning(url)
        props = (result or {}).get("properties", {})
        if props.get("provisioningState") != "Succeeded":
            raise RuntimeError(f"Agent Application '{cfg['app']}' did not provision")
        client_id = props.get("defaultInstanceIdentity", {}).get("clientId")
        if not client_id:
            raise RuntimeError(f"No clientId on Agent Application '{cfg['app']}'")
    azd_env_set(cfg["msa_env"], client_id)
    return client_id


def _routed_deployment(cfg):
    """(deploymentId, agentVersion) currently receiving traffic, or (None, None)."""
    app = _arm_rest("GET", _app_url(cfg["app"])) or {}
    rules = app.get("properties", {}).get("trafficRoutingPolicy", {}).get("rules") or []
    dep_id = rules[0].get("deploymentId") if rules else None
    if not dep_id:
        return None, None
    deployments = (_arm_rest("GET", _deployments_url(cfg["app"])) or {}).get("value", [])
    for d in deployments:
        if d.get("properties", {}).get("deploymentId") == dep_id:
            agents = d["properties"].get("agents") or [{}]
            return dep_id, str(agents[0].get("agentVersion"))
    return dep_id, None


def _ensure_versioned_deployment(cfg, agent_version):
    """Immutable deployment '<agent>-v<version>' (never repoint the live one in place)."""
    name = f"{cfg['agent']}-v{agent_version}"
    url = _deployments_url(cfg["app"], name)
    existing = _arm_rest("GET", url)
    props = existing.get("properties", {}) if isinstance(existing, dict) else {}
    if props.get("provisioningState") == "Succeeded" and props.get("deploymentId"):
        print(f"  Deployment '{name}' exists ({props['deploymentId']})")
        return props["deploymentId"]
    body = {"properties": {
        "displayName": f"{cfg['display']} v{agent_version}",
        "deploymentType": "Managed",
        "protocols": [{"protocol": "Responses", "version": "1.0"}],
        "agents": [{"agentName": cfg["agent"], "agentVersion": str(agent_version)}],
    }}
    print(f"  Creating deployment '{name}'...")
    result = _arm_rest("PUT", url, body)
    if result and result.get("properties", {}).get("provisioningState") != "Succeeded":
        result = _poll_provisioning(url)
    props = (result or {}).get("properties", {})
    if props.get("provisioningState") != "Succeeded" or not props.get("deploymentId"):
        raise RuntimeError(f"Deployment '{name}' failed: {props.get('provisioningState')}")
    return props["deploymentId"]


def _route_to(cfg, deployment_id):
    """Route 100% of the application's traffic to deployment_id and assert it."""
    app = _arm_rest("GET", _app_url(cfg["app"])) or {}
    props = app.get("properties", {})
    # Send only the scheme: echoing server-populated fields (isolationKeySource) is rejected.
    scheme = (props.get("authorizationPolicy") or {}).get("authorizationScheme", "Channels")
    body = {"properties": {
        "displayName": props.get("displayName", cfg["display"]),
        "agents": [{"agentName": cfg["agent"]}],
        "authorizationPolicy": {"authorizationScheme": scheme},
        "trafficRoutingPolicy": {"protocol": "FixedRatio", "rules": [
            {"ruleId": "default", "deploymentId": deployment_id, "trafficPercentage": 100}]},
    }}
    print(f"  Routing '{cfg['app']}' -> {deployment_id}...")
    routed = None
    for attempt in range(3):
        body_file = _write_temp_json(body)
        try:
            put = subprocess.run(
                f'az rest --method PUT --url "{_app_url(cfg["app"])}" '
                f'--headers "Content-Type=application/json" --body "@{body_file}"',
                capture_output=True, text=True, shell=True, encoding="utf-8", errors="replace",
                env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        finally:
            os.unlink(body_file)
        if put.returncode != 0:
            print(f"  Routing PUT attempt {attempt + 1} failed: {put.stderr.strip()[:300]}")
        for _ in range(9):  # the PUT is applied asynchronously
            routed, _version = _routed_deployment(cfg)
            if routed == deployment_id:
                break
            time.sleep(10)
        if routed == deployment_id:
            break
    if routed != deployment_id:
        raise RuntimeError(f"Routing assert failed for '{cfg['app']}': routed={routed}")
    print(f"  Routed (asserted): {routed}")


def smoke_test_agent_version(project_client, agent_name, agent_version):
    """Pre-route check of the exact version. Consent requests count as healthy plumbing."""
    client = project_client.get_openai_client()
    resp = client.responses.create(
        input="Health check: reply with the single word OK. Do not call any tool.",
        extra_body={"agent_reference": {"name": agent_name, "version": str(agent_version),
                                        "type": "agent_reference"}},
    )
    types = [getattr(i, "type", "") for i in resp.output or []]
    if getattr(resp, "status", "completed") not in ("completed", None) and \
            "oauth_consent_request" not in types:
        raise RuntimeError(f"Smoke test of {agent_name} v{agent_version}: status {resp.status}")
    print(f"  Smoke test {agent_name} v{agent_version}: ok (items {sorted(set(types))})")


def staged_publish(project_client, cfg, agent_version):
    """Record current route, deploy exact version, smoke test it, route 100%, assert."""
    prefix = cfg["env_prefix"]
    prev_id, prev_version = _routed_deployment(cfg)
    print(f"  Current route: {prev_id or 'none'} (agent v{prev_version or '?'})")
    dep_id = _ensure_versioned_deployment(cfg, agent_version)
    if prev_id == dep_id:
        print(f"  {cfg['agent']} v{agent_version} already receives 100% of traffic")
        return dep_id
    if prev_id:
        azd_env_set(f"{prefix}_PREV_DEPLOYMENT_ID", prev_id)
        azd_env_set(f"{prefix}_PREV_AGENT_VERSION", prev_version or "")
    try:
        smoke_test_agent_version(project_client, cfg["agent"], agent_version)
    except Exception:
        # The new version is already 'latest' (web chat); put the routed one back.
        if prev_version:
            _restore_as_latest(project_client, cfg["agent"], prev_version)
        raise
    _route_to(cfg, dep_id)
    azd_env_set(f"{prefix}_AGENT_VERSION", str(agent_version))
    azd_env_set(f"{prefix}_DEPLOYMENT_ID", dep_id)
    return dep_id


def rollback():
    """Restore each agent to the version recorded before the last publish.

    Routes the application (Teams) back to the previous deployment and republishes
    the previous definition as the newest version, because the web chat calls
    agent_reference without a version and always gets the latest one.
    """
    project_client = _project_client()
    failures = []
    for cfg in PUBLISHED_AGENTS:
        prefix = cfg["env_prefix"]
        prev = os.environ.get(f"{prefix}_PREV_DEPLOYMENT_ID", "")
        prev_version = os.environ.get(f"{prefix}_PREV_AGENT_VERSION", "")
        if not prev:
            print(f"  {cfg['app']}: no previous deployment recorded — skipped")
            continue
        try:
            _route_to(cfg, prev)
            azd_env_set(f"{prefix}_DEPLOYMENT_ID", prev)
            azd_env_set(f"{prefix}_AGENT_VERSION", prev_version)
            if prev_version:
                _restore_as_latest(project_client, cfg["agent"], prev_version)
        except Exception as e:
            failures.append(f"{cfg['app']}: {e}")
    if failures:
        raise RuntimeError("Rollback failed: " + "; ".join(failures))


# ---------------------------------------------------------------------------
# Bot Service + Teams (per published agent)
# ---------------------------------------------------------------------------

def _activity_endpoint(app_name):
    account = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")
    return (f"https://{account}.services.ai.azure.com/api/projects/{project}"
            f"/applications/{app_name}/protocols/activityprotocol"
            f"?api-version=2025-11-15-preview")


def create_bot_service_and_channels(cfg, msa_app_id):
    """Bootstrap the agent's Bot Service + channels (first run; Bicep manages it after).

    Adopts an existing bot that already uses msa_app_id (e.g. portal-created).
    """
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    env_name = os.environ.get("AZURE_ENV_NAME", "").lower()
    bot_name = os.environ.get(cfg["bot_env"]) or f"{cfg['bot_prefix']}-{env_name}"
    bots_base = (f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{rg}"
                 f"/providers/Microsoft.BotService/botServices")
    api = "api-version=2023-09-15-preview"

    existing = _arm_rest("GET", f"{bots_base}/{bot_name}?{api}")
    if existing and isinstance(existing, dict) and existing.get("id"):
        print(f"  Bot Service '{bot_name}' exists — Bicep manages it")
        azd_env_set(cfg["bot_env"], bot_name)
        return
    bots = _arm_rest("GET", f"{bots_base}?{api}") or {}
    for bot in bots.get("value", []):
        if bot.get("properties", {}).get("msaAppId") == msa_app_id:
            print(f"  Bot Service '{bot['name']}' already uses this msaAppId — adopting it")
            azd_env_set(cfg["bot_env"], bot["name"])
            return

    tenant_id = run("az account show --query tenantId -o tsv")
    print(f"  Creating Bot Service '{bot_name}'...")
    result = _arm_rest("PUT", f"{bots_base}/{bot_name}?{api}", {
        "location": "global", "kind": "azurebot", "sku": {"name": "S1"},
        "properties": {
            "displayName": cfg["display"], "description": "Bot service for AI agent",
            "endpoint": _activity_endpoint(cfg["app"]), "msaAppId": msa_app_id,
            "msaAppTenantId": tenant_id, "msaAppType": "SingleTenant",
        },
    })
    if not result:
        raise RuntimeError(f"Failed to create Bot Service '{bot_name}'")
    azd_env_set(cfg["bot_env"], bot_name)
    channels = {
        "MsTeamsChannel": {"isEnabled": True, "deploymentEnvironment": "CommercialDeployment"},
        "DirectLineChannel": {"sites": [{"siteName": "Default Site", "isEnabled": True,
                                         "isV1Enabled": True, "isV3Enabled": True}]},
    }
    for channel, props in channels.items():
        ok = _arm_rest("PUT", f"{bots_base}/{bot_name}/channels/{channel}?{api}", {
            "location": "global", "properties": {"channelName": channel, "properties": props}})
        print(f"  {channel}: {'created' if ok else 'FAILED'}")


def _next_manifest_version(existing_app):
    """Patch-bump the highest appDefinition version in the catalog (Teams rejects reuse)."""
    def parse(v):
        try:
            return tuple(int(x) for x in v.split("."))
        except (AttributeError, ValueError):
            return (0, 0, 0)
    versions = [parse(d.get("version")) for d in (existing_app or {}).get("appDefinitions", [])]
    if not versions:
        return "1.0.0"
    major, minor, patch = (list(max(versions)) + [0, 0, 0])[:3]
    return f"{major}.{minor}.{patch + 1}"


def publish_teams_app_org_wide(cfg, msa_app_id):
    """Publish/update the agent's Teams app in the org catalog via Graph (non-fatal)."""
    import zipfile

    env_name = os.environ.get("AZURE_ENV_NAME", "default")
    developer_name = os.environ.get("TEAMS_APP_DEVELOPER_NAME", "")
    privacy_url = os.environ.get("TEAMS_APP_PRIVACY_URL", "")
    terms_url = os.environ.get("TEAMS_APP_TERMS_URL", "")
    if not developer_name or not privacy_url or not terms_url:
        print("  Skipping Teams publish — set TEAMS_APP_DEVELOPER_NAME, "
              "TEAMS_APP_PRIVACY_URL and TEAMS_APP_TERMS_URL")
        return

    app_external_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{cfg['app']}-{env_name}"))
    lookup = run(
        f'az rest --method GET --url "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps'
        f'?%24filter=externalId eq \'{app_external_id}\'&%24expand=appDefinitions"',
        parse_json=True,
    )
    if lookup is None:
        print("  WARNING: cannot read the Teams org catalog (needs AppCatalog.ReadWrite.All "
              "on the deployer token) — skipped")
        return
    apps = lookup.get("value", []) if isinstance(lookup, dict) else []
    existing = apps[0] if apps else None
    if not existing:
        # An app uploaded outside the hook (Teams admin center, Foundry portal) keeps its own
        # externalId and manifest. Publishing ours would add a duplicate app for the same bot.
        org_apps = run(
            'az rest --method GET --url "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps'
            '?%24filter=distributionMethod eq \'organization\''
            '&%24expand=appDefinitions(%24expand=bot)"',
            parse_json=True,
        ) or {}
        for app in org_apps.get("value", []):
            if any((d.get("bot") or {}).get("id") == msa_app_id
                   for d in app.get("appDefinitions") or []):
                print(f"  Teams app '{app.get('displayName')}' ({app['id']}) already serves this "
                      "bot — adopting it as is (not published by the hook, so not updated)")
                return
    version = _next_manifest_version(existing)

    manifest = {
        "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.19/MicrosoftTeams.schema.json",
        "manifestVersion": "1.19",
        "version": version,
        "id": app_external_id,
        "developer": {"name": developer_name, "websiteUrl": privacy_url,
                      "privacyUrl": privacy_url, "termsOfUseUrl": terms_url},
        "name": {"short": cfg["display"], "full": f"{cfg['display']} ({env_name})"},
        "description": {"short": cfg["desc_short"], "full": cfg["desc_full"]},
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": "#0078D4",
        "bots": [{"botId": msa_app_id, "scopes": ["personal", "team", "groupChat"],
                  "supportsFiles": False, "isNotificationOnly": False, "commandLists": []}],
        "permissions": ["identity", "messageTeamMembers"],
        "validDomains": [],
    }
    assets_dir = os.path.join(REPO_ROOT, "assets", "teams")
    zip_path = os.path.join(tempfile.gettempdir(), f"teams-app-{cfg['app']}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for icon in ("color.png", "outline.png"):
            if os.path.exists(os.path.join(assets_dir, icon)):
                zf.write(os.path.join(assets_dir, icon), icon)

    try:
        if existing:
            print(f"  Updating Teams app {existing['id']} -> v{version}...")
            url = (f"https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/"
                   f"{existing['id']}/appDefinitions")
        else:
            print(f"  Publishing Teams app v{version} to the org catalog...")
            url = "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps?requiresReview=false"
        result = run(f'az rest --method POST --url "{url}" '
                     f'--headers "Content-Type=application/zip" --body "@{zip_path}"',
                     parse_json=True)
        if result is None:
            print("  WARNING: Teams publish failed (needs AppCatalog.ReadWrite.All)")
        else:
            print(f"  Teams app '{cfg['display']}' v{version} published")
    finally:
        try:
            os.unlink(zip_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _non_fatal(step, fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        print(f"\nWARNING: {step} failed (non-fatal): {e}")
        traceback.print_exc()
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="azd postprovision hook (v3)")
    parser.add_argument("--rollback", action="store_true",
                        help="route each agent back to the deployment recorded before "
                             "the last publish, then exit")
    args = parser.parse_args()

    if args.rollback:
        print("=== Rollback: restore previous agent deployments ===")
        rollback()
        print("=== Rollback complete ===")
        return

    print("=== Post-provision hook (OBO, v3) ===\n")

    print("--- Step 0: Certificate upload + APIM binding ---")
    _non_fatal("Certificate upload", upload_cert_and_configure_apim)

    print("\n--- Step 1: Chat App Entra registration ---")
    _non_fatal("Chat App Entra registration", create_chat_app_entra_registration)

    # Agents reference the connection by name, so it must exist first.
    print("\n--- Step 2: Salesforce OBO connection ---")
    _non_fatal("SF OBO connection", ensure_obo_connection)

    print("\n--- Step 3: Chat App settings ---")
    _non_fatal("Chat App settings update", update_chat_app_settings)

    print("\n--- Step 4: OBO APIM Named Values ---")
    _non_fatal("OBO APIM Named Values update", update_obo_apim_named_values)

    # From here on, failures are fatal: a half-published agent is worse than a failed hook.
    project_client = _project_client()

    print("\n--- Step 5: Skills ---")
    skill_versions = _with_propagation_retry(lambda: ensure_skills(project_client), "skills")

    print("\n--- Step 6: Agents ---")
    versions = {"salesforce-assistant": create_agent(project_client, skill_versions),
                "customer360-assistant": create_customer360_agent(project_client, skill_versions)}

    msa_ids = {}
    for cfg in PUBLISHED_AGENTS:
        version = versions.get(cfg["agent"])
        if not version:
            print(f"\n--- Step 7: {cfg['display']} publish (skipped — no agent version) ---")
            continue
        print(f"\n--- Step 7: {cfg['display']} — application + staged publish (v{version}) ---")
        msa_ids[cfg["agent"]] = ensure_agent_application(cfg)
        staged_publish(project_client, cfg, version)

    for cfg in PUBLISHED_AGENTS:
        msa = msa_ids.get(cfg["agent"])
        if not msa:
            continue
        print(f"\n--- Step 8: {cfg['display']} — Bot Service ---")
        create_bot_service_and_channels(cfg, msa)
        print(f"\n--- Step 9: {cfg['display']} — Teams org catalog ---")
        _non_fatal("Teams publish", publish_teams_app_org_wide, cfg, msa)

    print("\n=== Post-provision hook complete ===")
    print("Rollback: python hooks/postprovision.py --rollback")


if __name__ == "__main__":
    main()
