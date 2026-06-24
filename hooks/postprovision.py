"""Post-provision hook: upload cert, create Entra app, configure auth, create Foundry agent,
deploy agent application, bootstrap Bot Service, publish Teams app.

After Bicep deploys Azure resources, this hook:
0. Uploads SF JWT Bearer cert to Key Vault + creates APIM cert binding (if cert exists locally)
1. Creates Chat App Entra app registration (az CLI — delegated permissions)
2. Creates the Foundry agent with Salesforce MCP tool (OBO connection)
3. Updates Chat App Container App env vars
4. Recreates OBO connection via ARM REST + updates APIM Named Values
5. Creates/updates Agent Application (REST-only — enables Activity Protocol endpoint)
6. Creates/updates Agent Deployment (links agent version to application)
6b. Creates Customer 360 agent (dual MCP: SF + SN, requires servicenow-obo connection)
6c. Creates Customer 360 Agent Application
6d. Creates Customer 360 Agent Deployment
7. Bootstraps Bot Service + channels via ARM REST (first-run only — Bicep takes over after)
8. Publishes Teams app to org catalog via Graph API (org-wide distribution)

Uses az CLI for Entra ops because the Graph Bicep extension requires
Application.ReadWrite.All on the ARM deployment identity, which is not
available in managed tenants.

Uses azure-ai-projects v2 SDK for Foundry agent (no ARM resource type).
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


def update_obo_connection():
    """Recreate the OBO connection via ARM REST to ensure it's properly registered.

    The OBO connection uses authType UserEntraToken — Foundry passes the user's
    Azure AD token through to APIM, where APIM handles the SF token exchange.
    Note: authType 'AAD' is NOT valid for RemoteTool connections.
    """
    connection_name = os.environ.get("SF_OBO_CONNECTION_NAME", "salesforce-obo")
    sf_mcp_obo_endpoint = os.environ.get("APIM_SF_MCP_OBO_ENDPOINT", "")

    if not sf_mcp_obo_endpoint:
        apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
        if apim_gateway:
            sf_mcp_obo_endpoint = f"{apim_gateway}/salesforce-mcp-obo/mcp"
    if not sf_mcp_obo_endpoint:
        print("  WARNING: No SF MCP OBO endpoint — skipping connection update")
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

    # Delete and recreate to ensure proper registration
    print(f"  Deleting Bicep-created connection '{connection_name}'...")
    run(f'az rest --method DELETE --url "{url}"')

    body = {
        "properties": {
            "authType": "UserEntraToken",
            "category": "RemoteTool",
            "target": sf_mcp_obo_endpoint,
            "audience": "https://ai.azure.com",
            "metadata": {"type": "custom_MCP"},
            "isSharedToAll": True,
        }
    }

    body_file = _write_temp_json(body)
    try:
        print(f"  Recreating connection '{connection_name}' via ARM REST...")
        result = run(
            f'az rest --method PUT --url "{url}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
        if result:
            print("  SF OBO connection created")
        else:
            print("  WARNING: Failed to create SF OBO connection")
    finally:
        os.unlink(body_file)


def create_memory_store(project_client):
    """Create the project-memory store (idempotent — get-or-create).

    Uses text-embedding-3-small for embeddings and gpt-5.4 for chat summaries.
    Returns the store name on success, None on failure.
    """
    from azure.ai.projects.models import (
        MemoryStoreDefaultDefinition,
        MemoryStoreDefaultOptions,
    )

    store_name = "project-memory"
    print(f"\n  Creating memory store '{store_name}'...")

    # Check if store already exists
    try:
        existing = project_client.memory_stores.get(name=store_name)
        if existing:
            print(f"  Memory store already exists: {store_name}")
            return store_name
    except Exception:
        pass  # Store doesn't exist — create it

    # Retry with backoff (same propagation delay as agent creation)
    max_retries = 4
    retry_delay = 10
    for attempt in range(max_retries):
        try:
            project_client.memory_stores.create(
                name=store_name,
                definition=MemoryStoreDefaultDefinition(
                    chat_model="gpt-5.4",
                    embedding_model="text-embedding-3-small",
                    options=MemoryStoreDefaultOptions(
                        user_profile_enabled=True,
                        chat_summary_enabled=True,
                        user_profile_details="Salesforce user. Track their common objects, field names, query patterns, role, department, and error patterns.",
                    ),
                ),
            )
            print(f"  Memory store created: {store_name}")
            return store_name
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: {e}")
                print(f"  Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            else:
                print(f"  WARNING: Failed to create memory store: {e}")
                return None


def create_agent():
    """Create a Foundry agent with the Salesforce MCP tool using the v2 SDK.

    Uses the OBO connection (UserEntraToken) and the OBO APIM endpoint.
    Includes MemorySearchPreviewTool for per-user conversational memory.
    Returns the agent version number (for use by create_agent_deployment).
    """
    project_endpoint = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT")

    if not project_endpoint:
        print("WARNING: Missing AI_FOUNDRY_PROJECT_ENDPOINT — skipping agent creation.")
        return None

    sf_mcp_endpoint = os.environ.get("APIM_SF_MCP_OBO_ENDPOINT", "")
    if not sf_mcp_endpoint:
        apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
        if apim_gateway:
            sf_mcp_endpoint = f"{apim_gateway}/salesforce-mcp-obo/mcp"
    connection_name = os.environ.get("SF_OBO_CONNECTION_NAME", "salesforce-obo")

    if not sf_mcp_endpoint:
        print("WARNING: No SF MCP endpoint available — skipping agent creation.")
        return None

    print(f"\nProject endpoint: {project_endpoint}")
    print(f"SF MCP endpoint:  {sf_mcp_endpoint}")
    print(f"Connection:       {connection_name}")

    from azure.identity import DefaultAzureCredential
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import (
        PromptAgentDefinition, MCPTool, MemorySearchPreviewTool,
    )

    credential = DefaultAzureCredential()
    project_client = AIProjectClient(
        endpoint=project_endpoint,
        credential=credential,
    )

    agent_name = "salesforce-assistant"
    print(f"\nCreating agent '{agent_name}'...")

    # Build Salesforce MCPTool
    sf_tool_kwargs = {
        "server_label": "salesforce_mcp",
        "server_url": sf_mcp_endpoint,
        "require_approval": "never",
        "allowed_tools": [
            "whoami",
            "list_objects",
            "describe_object",
            "soql_query",
            "search_records",
            "write_record",
            "process_approval",
        ],
    }

    if connection_name:
        sf_tool_kwargs["project_connection_id"] = connection_name
        print(f"Connection: {connection_name}")

    sf_mcp_tool = MCPTool(**sf_tool_kwargs)
    tools = [sf_mcp_tool]

    # Create memory store and add MemorySearchPreviewTool
    store_name = create_memory_store(project_client)
    if store_name:
        memory_tool = MemorySearchPreviewTool(
            memory_store_name=store_name,
            scope="{{$userId}}",
            update_delay=300,
        )
        tools.append(memory_tool)
        print(f"  MemorySearchPreviewTool added (store={store_name}, scope=per-user)")

    instructions = """\
You are an assistant with access to Salesforce via MCP tools.

## Memory
You have access to a memory store that remembers details from past conversations with each user.
- Memory is automatically populated from conversations. No explicit save is needed.
- Use memory when you lack information that a past conversation might have provided \
(e.g., user's UserId, preferred objects, common query patterns).
- Do NOT call memory_search if the answer is already in the current conversation history.
- ALWAYS call describe_object(mode="full") before writes regardless of memory — picklist \
values and validation rules can change at any time.

## Workflow
1. Plan — tell the user what you intend to do before calling tools.
2. whoami — if the user says "my" or refers to themselves, use their UserId if it is \
already in the conversation or memory. Only call whoami if you have neither. \
Use UserId as OwnerId or CreatedById in SOQL WHERE clauses.
3. list_objects — find the API name (use `name`, not `label`).
4. describe_object — REQUIRED before create/update/upsert/delete (use mode="full").
   For reads, skip if you know the fields (from memory or prior turns), or use mode="slim" \
to discover fields and relationships.
   Slim returns referenceTo (lookup targets) and childRelationships (for subqueries).
5. Execute — soql_query, search_records, write_record, or process_approval.
6. Summarize — present results in plain language. Do NOT dump raw JSON.

## Common fields (no describe needed)
Id, Name, CreatedDate, OwnerId, LastModifiedDate — available on all standard objects.

## Error recovery — CRITICAL
- On INVALID_FIELD or MALFORMED_QUERY: the error response includes `availableFields` with \
{name, type, referenceTo} for every field on the object. Do NOT call describe_object — \
use availableFields to fix your query and retry immediately.
- If a field you expected is missing from availableFields, the org restricts it. \
Check childRelationships on the parent object for an alternative path (subquery).
- INSUFFICIENT_ACCESS — user lacks permission. Explain clearly.
- ENTITY_IS_DELETED — record was deleted. Inform user.
- UNABLE_TO_LOCK_ROW — retry once after a moment.

## Rules
- Do NOT guess field names — use describe_object (slim for reads, full for writes).
- ALWAYS confirm with the user before any create, update, upsert, or delete operation.
  Describe the exact change (object, record, fields, values) and wait for explicit approval.
- Always include LIMIT in SOQL unless the user specifically requests all rows.
- All API names are PascalCase: Account, OpportunityLineItem, Custom_Field__c.
- For subqueries use relationshipName from childRelationships, not the object name.
"""

    # Retry with backoff — after fresh deploy, the Foundry data plane
    # takes 5-15 min to propagate. "Project not found" is transient.
    max_retries = 6
    retry_delay = 10
    for attempt in range(max_retries):
        try:
            agent = project_client.agents.create_version(
                agent_name=agent_name,
                definition=PromptAgentDefinition(
                    model="gpt-5.4",
                    instructions=instructions,
                    tools=tools,
                ),
            )
            print(f"Agent created: name={agent.name}, version={agent.version}, id={agent.id}")
            print(f"  Tools: {len(tools)} tool(s) configured")
            print("\nOBO flow requires no consent. Send a chat message to test.")
            print(f"Agent: {agent.name} v{agent.version}")
            return agent.version
        except Exception as e:
            if "not found" in str(e).lower() and attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: {e}")
                print(f"  Retrying in {retry_delay}s (waiting for project propagation)...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            else:
                raise


def create_customer360_agent():
    """Create a Foundry agent with both Salesforce and ServiceNow MCP tools.

    Connects to both salesforce-obo and servicenow-obo connections for unified
    CRM+ITSM queries. Pre-checks that servicenow-obo connection exists.
    Returns the agent version number.
    """
    project_endpoint = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT")
    if not project_endpoint:
        print("WARNING: Missing AI_FOUNDRY_PROJECT_ENDPOINT — skipping Customer 360 agent.")
        return None

    # SF MCP endpoint
    sf_mcp_endpoint = os.environ.get("APIM_SF_MCP_OBO_ENDPOINT", "")
    if not sf_mcp_endpoint:
        apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
        if apim_gateway:
            sf_mcp_endpoint = f"{apim_gateway}/salesforce-mcp-obo/mcp"
    sf_connection = os.environ.get("SF_OBO_CONNECTION_NAME", "salesforce-obo")

    if not sf_mcp_endpoint:
        print("WARNING: No SF MCP endpoint available — skipping Customer 360 agent.")
        return None

    # SN MCP endpoint — derive from APIM gateway
    apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
    sn_mcp_endpoint = f"{apim_gateway}/servicenow-mcp-obo/mcp" if apim_gateway else ""
    sn_connection = "servicenow-obo"

    if not sn_mcp_endpoint:
        print("WARNING: No SN MCP endpoint available — skipping Customer 360 agent.")
        return None

    # Pre-check: verify servicenow-obo connection exists in Foundry
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    account = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project_name = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")
    conn_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        f"/projects/{project_name}/connections/{sn_connection}"
        f"?api-version=2025-04-01-preview"
    )
    conn_check = run(
        f'az rest --method GET --url "{conn_url}"',
        parse_json=True,
    )
    if not conn_check or not isinstance(conn_check, dict):
        print(f"WARNING: '{sn_connection}' connection not found in Foundry project.")
        print("  Deploy snow-meta-tool first (azd up), then re-run.")
        print("  Skipping Customer 360 agent creation.")
        return None

    print(f"\nProject endpoint: {project_endpoint}")
    print(f"SF MCP endpoint:  {sf_mcp_endpoint}")
    print(f"SF Connection:    {sf_connection}")
    print(f"SN MCP endpoint:  {sn_mcp_endpoint}")
    print(f"SN Connection:    {sn_connection}")

    from azure.identity import DefaultAzureCredential
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import (
        PromptAgentDefinition, MCPTool, MemorySearchPreviewTool,
    )

    credential = DefaultAzureCredential()
    project_client = AIProjectClient(
        endpoint=project_endpoint,
        credential=credential,
    )

    agent_name = "customer360-assistant"
    print(f"\nCreating agent '{agent_name}'...")

    # Build Salesforce MCPTool
    sf_mcp_tool = MCPTool(
        server_label="salesforce_mcp",
        server_url=sf_mcp_endpoint,
        project_connection_id=sf_connection,
        require_approval="never",
        allowed_tools=[
            "whoami",
            "list_objects",
            "describe_object",
            "soql_query",
            "search_records",
            "write_record",
            "process_approval",
        ],
    )

    # Build ServiceNow MCPTool
    sn_mcp_tool = MCPTool(
        server_label="servicenow_mcp",
        server_url=sn_mcp_endpoint,
        project_connection_id=sn_connection,
        require_approval="never",
        allowed_tools=["discover", "query", "write"],
    )

    tools = [sf_mcp_tool, sn_mcp_tool]

    # Create memory store and add MemorySearchPreviewTool (skip if DISABLE_AGENT_MEMORY=true)
    if os.environ.get("DISABLE_AGENT_MEMORY", "").lower() == "true":
        print("  MemorySearchPreviewTool SKIPPED (DISABLE_AGENT_MEMORY=true)")
    else:
        store_name = create_memory_store(project_client)
        if store_name:
            memory_tool = MemorySearchPreviewTool(
                memory_store_name=store_name,
                scope="{{$userId}}",
                update_delay=300,
            )
            tools.append(memory_tool)
            print(f"  MemorySearchPreviewTool added (store={store_name}, scope=per-user)")

    instructions = """\
You are a Customer 360 assistant with access to Salesforce (CRM) and ServiceNow (ITSM) \
via MCP tools. Provide a unified view of customers across both systems.

## Memory
Per-user memory is auto-populated — no explicit save needed.
Do NOT query memory if the answer is already in the current conversation.
NEVER answer data questions from memory alone — always call tools for fresh data. \
Memory is for user preferences and metadata, not for record data.

## Workflow
1. Plan — tell the user what you will query in each system before calling tools.
2. Query both systems using tools — always fetch fresh data, never rely on memory or \
prior turns for record-level answers.
3. Correlate by company name and synthesize a unified view.

## Correlation
- Company name: SF Account.Name = SN company field on incidents/problems/changes.
- Keywords: SF Case.Subject/Description keywords match SN Incident short_description.

## Business Insights
- Revenue at risk: cross-reference P1/P2 incidents with SF accounts' open opportunities — \
show pipeline value at risk. Lead with monetary amounts.
- Case-incident correlation: SF Cases and SN Incidents for the same company with similar \
keywords are likely the same issue from different angles.

## Salesforce Rules
- whoami: use cached UserId for "my" queries; call only if not in memory or context.
- describe_object: REQUIRED before writes (mode="full"). Skip for reads if fields known; \
use mode="slim" to discover fields and relationships.
- Common fields need no describe: Id, Name, CreatedDate, OwnerId, LastModifiedDate.
- INVALID_FIELD/MALFORMED_QUERY errors include availableFields — fix and retry, no re-describe.
- API names are PascalCase. Always include LIMIT in SOQL. Use relationshipName for subqueries.

## ServiceNow Rules
- discover(table=...): REQUIRED before writes; optional for reads if fields known. \
Use mode="names" for validation only; mode="compact" (default) for full metadata.
- Skip discover(filter=...) if you already know the table name.
- ALWAYS pass fields= to query — only the columns needed.
- Encoded query: field=value, fieldLIKEvalue, ^(AND), ^OR, ^ORDERBYDESCfield.
- sys_id (32-char hex) for updates/deletes.
- 403 on discover: fall back to standard fields (short_description, priority, state, \
urgency, impact, assignment_group, assigned_to, description, category).
- Company lookup: use a SINGLE query with OR to cover exact and variant matches. \
Example: company=Contoso Ltd^ORcompanyLIKEContoso. Do NOT issue separate exact and LIKE queries.
- When querying multiple companies, combine them in one query with OR: \
company=Acme Corp^ORcompany=Contoso Ltd^ORcompany=Northwind Traders.

## Rules
- Confirm before any write in either system.
- When correlating, explain what matched and why.
- Lead with monetary amounts — they drive decisions.
"""

    # Retry with backoff
    max_retries = 6
    retry_delay = 10
    for attempt in range(max_retries):
        try:
            agent = project_client.agents.create_version(
                agent_name=agent_name,
                definition=PromptAgentDefinition(
                    model="gpt-5.4",
                    instructions=instructions,
                    tools=tools,
                ),
            )

            # Add conversation starters via REST API (SDK doesn't expose this yet).
            # Foundry UI stores starters in TWO places:
            #   1. definition.conversation_starters — array of {"text": "..."}
            #   2. metadata.starterPrompts — newline-separated string (UI reads this)
            # We read the SDK-created version, add both fields, and POST a new version.
            _starters = [
                "Give me a Customer 360 view for Contoso Ltd",
                "Acme Corp reports API gateway outages \u2014 what's the full picture?",
                "Prepare me for my call with Fabrikam Inc tomorrow",
                "Which strategic accounts have the most open incidents? Show revenue at risk",
                "Show me unresolved P1 incidents and correlated Salesforce cases",
                "P1/P2 incidents with affected Salesforce accounts \u2014 rank by revenue at risk",
            ]
            final_version = agent.version
            try:
                import httpx as _httpx
                _token = credential.get_token("https://ai.azure.com/.default").token
                _headers = {"Authorization": f"Bearer {_token}", "Content-Type": "application/json"}
                _params = {"api-version": "2025-05-15-preview"}
                _r = _httpx.get(
                    f"{project_endpoint}/agents/{agent_name}/versions/{agent.version}",
                    headers=_headers, params=_params, timeout=30,
                )
                if _r.status_code == 200:
                    _body = _r.json()
                    _defn = _body["definition"]
                    _defn["conversation_starters"] = [{"text": s} for s in _starters]
                    _meta = _body.get("metadata", {})
                    _meta["starterPrompts"] = "\n".join(_starters)
                    _r2 = _httpx.post(
                        f"{project_endpoint}/agents/{agent_name}/versions",
                        headers=_headers, params=_params,
                        json={"definition": _defn, "metadata": _meta},
                        timeout=30,
                    )
                    if _r2.status_code == 200:
                        final_version = _r2.json().get("version", agent.version)
                        print(f"  Conversation starters added (v{final_version})")
                    else:
                        print(f"  WARNING: Failed to add starters: {_r2.status_code}")
            except Exception as _e:
                print(f"  WARNING: conversation starters skipped: {_e}")
            print(f"Agent created: name={agent.name}, version={final_version}, id={agent.id}")
            print(f"  Tools: {len(tools)} tool(s) configured (SF MCP + SN MCP + Memory)")
            print(f"Agent: {agent.name} v{final_version}")
            return final_version
        except Exception as e:
            if "not found" in str(e).lower() and attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: {e}")
                print(f"  Retrying in {retry_delay}s (waiting for project propagation)...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
            else:
                raise


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
    """Build the ARM control-plane base URL for the Foundry project.

    Returns e.g. https://management.azure.com/subscriptions/.../accounts/.../projects/...
    """
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    account = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")
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
            if state == "Succeeded":
                return result
            if state in ("Failed", "Canceled"):
                print(f"  Provisioning failed: {state}")
                return result
            print(f"  Provisioning state: {state} (waiting...)")
        time.sleep(interval)
        elapsed += interval
    print(f"  WARNING: Polling timed out after {timeout}s")
    return None


def create_agent_application():
    """Create/update the Agent Application via ARM control plane.

    Returns the Foundry-managed identity clientId (msaAppId for Bot Service).
    Persists AGENT_BOT_MSA_APP_ID via azd env set.
    """
    app_name = "salesforce-assistant"
    agent_name = "salesforce-assistant"
    api_version = "2026-01-15-preview"

    base = _arm_project_base()
    if not base or "None" in base:
        print("  WARNING: Missing ARM project vars — skipping")
        return None

    url = f"{base}/applications/{app_name}?api-version={api_version}"

    # Check if already exists
    existing = _arm_rest("GET", url)
    if existing and isinstance(existing, dict):
        client_id = (
            existing.get("properties", {})
            .get("defaultInstanceIdentity", {})
            .get("clientId")
        )
        if client_id:
            print(f"  Agent Application already exists (clientId: {client_id})")
            azd_env_set("AGENT_BOT_MSA_APP_ID", client_id)
            return client_id

    # Create/update
    body = {
        "properties": {
            "displayName": "Salesforce Assistant",
            "agents": [{"agentName": agent_name}],
            "authorizationPolicy": {
                "authorizationScheme": "Channels",
            },
        }
    }

    print(f"  Creating Agent Application '{app_name}'...")
    result = _arm_rest("PUT", url, body)
    if not result:
        print("  ERROR: Failed to create Agent Application")
        return None

    # Check if already provisioned (PUT may return Succeeded immediately)
    state = result.get("properties", {}).get("provisioningState", "")
    if state != "Succeeded":
        print("  Waiting for provisioning...")
        result = _poll_provisioning(url)
        if not result:
            print("  ERROR: Agent Application provisioning timed out")
            return None

    client_id = (
        result.get("properties", {})
        .get("defaultInstanceIdentity", {})
        .get("clientId")
    )
    if not client_id:
        print("  ERROR: No clientId in Agent Application response")
        print(f"  Response: {json.dumps(result, indent=2)[:500]}")
        return None

    print(f"  Agent Application created (clientId: {client_id})")
    azd_env_set("AGENT_BOT_MSA_APP_ID", client_id)
    return client_id


def create_customer360_application():
    """Create/update the Customer 360 Agent Application via ARM control plane.

    No Bot Service or Teams app needed — chat-app discovers it automatically.
    """
    app_name = "customer360-assistant"
    agent_name = "customer360-assistant"
    api_version = "2026-01-15-preview"

    base = _arm_project_base()
    if not base or "None" in base:
        print("  WARNING: Missing ARM project vars — skipping")
        return None

    url = f"{base}/applications/{app_name}?api-version={api_version}"

    # Check if already exists
    existing = _arm_rest("GET", url)
    if existing and isinstance(existing, dict):
        client_id = (
            existing.get("properties", {})
            .get("defaultInstanceIdentity", {})
            .get("clientId")
        )
        if client_id:
            print(f"  Agent Application already exists (clientId: {client_id})")
            return client_id

    # Create/update
    body = {
        "properties": {
            "displayName": "Customer 360 Assistant",
            "agents": [{"agentName": agent_name}],
            "authorizationPolicy": {
                "authorizationScheme": "Channels",
            },
        }
    }

    print(f"  Creating Agent Application '{app_name}'...")
    result = _arm_rest("PUT", url, body)
    if not result:
        print("  ERROR: Failed to create Agent Application")
        return None

    # Check if already provisioned
    state = result.get("properties", {}).get("provisioningState", "")
    if state != "Succeeded":
        print("  Waiting for provisioning...")
        result = _poll_provisioning(url)
        if not result:
            print("  ERROR: Agent Application provisioning timed out")
            return None

    client_id = (
        result.get("properties", {})
        .get("defaultInstanceIdentity", {})
        .get("clientId")
    )
    if not client_id:
        print("  ERROR: No clientId in Agent Application response")
        print(f"  Response: {json.dumps(result, indent=2)[:500]}")
        return None

    print(f"  Agent Application created (clientId: {client_id})")
    return client_id


def create_agent_deployment(agent_version):
    """Create/update the Agent Deployment via ARM control plane.

    Uses fixed deployment name 'salesforce-assistant' as a 'latest' pointer.
    """
    app_name = "salesforce-assistant"
    deployment_name = "salesforce-assistant"
    agent_name = "salesforce-assistant"
    api_version = "2026-01-15-preview"

    base = _arm_project_base()
    if not base or "None" in base:
        print("  WARNING: Missing ARM project vars — skipping")
        return

    url = f"{base}/applications/{app_name}/agentDeployments/{deployment_name}?api-version={api_version}"

    body = {
        "properties": {
            "displayName": "Salesforce Assistant",
            "deploymentType": "Managed",
            "protocols": [
                {"protocol": "Responses", "version": "1.0"},
            ],
            "agents": [
                {
                    "agentName": agent_name,
                    "agentVersion": str(agent_version),
                },
            ],
        }
    }

    print(f"  Creating/updating Agent Deployment '{deployment_name}' (agent v{agent_version})...")
    result = _arm_rest("PUT", url, body)
    if not result:
        print("  ERROR: Failed to create Agent Deployment")
        return None

    # Check if already provisioned
    state = result.get("properties", {}).get("provisioningState", "")
    if state != "Succeeded":
        result = _poll_provisioning(url)
        if result:
            state = result.get("properties", {}).get("provisioningState", "")

    deployment_id = result.get("properties", {}).get("deploymentId", "") if result else ""
    print(f"  Agent Deployment: {state or 'unknown'} (deploymentId: {deployment_id})")

    # Update Application traffic routing to point to this deployment.
    # Without this, the Activity Protocol (Teams/Copilot) routes to a stale
    # deployment ID and returns "Sorry, I wasn't able to respond".
    if deployment_id:
        _update_traffic_routing(app_name, deployment_id, api_version)

    return deployment_id


def _update_traffic_routing(app_name, deployment_id, api_version):
    """Update Agent Application trafficRoutingPolicy to route to the given deployment."""
    base = _arm_project_base()
    url = f"{base}/applications/{app_name}?api-version={api_version}"

    body = {
        "properties": {
            "trafficRoutingPolicy": {
                "protocol": "FixedRatio",
                "rules": [
                    {
                        "ruleId": "default",
                        "deploymentId": deployment_id,
                        "trafficPercentage": 100,
                    }
                ],
            },
        }
    }

    print(f"  Updating traffic routing -> deploymentId={deployment_id}...")
    result = _arm_rest("PUT", url, body)
    if result:
        routed = (
            result.get("properties", {})
            .get("trafficRoutingPolicy", {})
            .get("rules", [{}])[0]
            .get("deploymentId", "")
        )
        print(f"  Traffic routing updated (routed to: {routed})")
    else:
        print("  WARNING: Failed to update traffic routing")


def create_customer360_deployment(agent_version):
    """Create/update the Customer 360 Agent Deployment via ARM control plane.

    Uses fixed deployment name 'customer360-assistant' as a 'latest' pointer.
    """
    app_name = "customer360-assistant"
    deployment_name = "customer360-assistant"
    agent_name = "customer360-assistant"
    api_version = "2026-01-15-preview"

    base = _arm_project_base()
    if not base or "None" in base:
        print("  WARNING: Missing ARM project vars — skipping")
        return

    url = f"{base}/applications/{app_name}/agentDeployments/{deployment_name}?api-version={api_version}"

    body = {
        "properties": {
            "displayName": "Customer 360 Assistant",
            "deploymentType": "Managed",
            "protocols": [
                {"protocol": "Responses", "version": "1.0"},
            ],
            "agents": [
                {
                    "agentName": agent_name,
                    "agentVersion": str(agent_version),
                },
            ],
        }
    }

    print(f"  Creating/updating Agent Deployment '{deployment_name}' (agent v{agent_version})...")
    result = _arm_rest("PUT", url, body)
    if not result:
        print("  ERROR: Failed to create Agent Deployment")
        return None

    # Check if already provisioned
    state = result.get("properties", {}).get("provisioningState", "")
    if state != "Succeeded":
        result = _poll_provisioning(url)
        if result:
            state = result.get("properties", {}).get("provisioningState", "")

    deployment_id = result.get("properties", {}).get("deploymentId", "") if result else ""
    print(f"  Agent Deployment: {state or 'unknown'} (deploymentId: {deployment_id})")

    if deployment_id:
        _update_traffic_routing(app_name, deployment_id, api_version)

    return deployment_id


def create_bot_service_and_channels(msa_app_id):
    """Create Bot Service + channels via ARM REST (first-run bootstrap only).

    On subsequent deploys, Bicep manages the Bot Service. This function
    skips if the Bot Service already exists.
    """
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    env_name = os.environ.get("AZURE_ENV_NAME", "")
    base_name = env_name.lower()
    bot_name = f"agent-bot-{base_name}"

    if not sub_id or not rg:
        print("  WARNING: Missing subscription ID or resource group — skipping")
        return

    bot_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.BotService/botServices/{bot_name}"
        f"?api-version=2023-09-15-preview"
    )

    # Check if our bot already exists by name
    existing = _arm_rest("GET", bot_url)
    if existing and isinstance(existing, dict) and existing.get("id"):
        print(f"  Bot Service '{bot_name}' already exists — Bicep will manage it")
        return

    # Check if any bot in the RG already uses this msaAppId (e.g. portal-created)
    list_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.BotService/botServices"
        f"?api-version=2023-09-15-preview"
    )
    bots = _arm_rest("GET", list_url)
    if bots and isinstance(bots, dict):
        for bot in bots.get("value", []):
            if bot.get("properties", {}).get("msaAppId") == msa_app_id:
                existing_name = bot.get("name", "unknown")
                print(f"  Bot Service '{existing_name}' already uses msaAppId — adopting it")
                azd_env_set("AGENT_BOT_NAME", existing_name)
                return

    # Build endpoint URL — Foundry activity protocol
    account = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")
    app_name = "salesforce-assistant"
    if not account or not project:
        print("  WARNING: COGNITIVE_ACCOUNT_NAME or AI_FOUNDRY_PROJECT_NAME not set")
        return
    endpoint = (
        f"https://{account}.services.ai.azure.com/api/projects/{project}"
        f"/applications/{app_name}/protocols/activityprotocol"
        f"?api-version=2025-11-15-preview"
    )

    tenant_id = run("az account show --query tenantId -o tsv")

    # Create Bot Service
    bot_body = {
        "location": "global",
        "kind": "azurebot",
        "sku": {"name": "S1"},
        "properties": {
            "displayName": "Salesforce Assistant",
            "description": "Bot service for AI agent",
            "endpoint": endpoint,
            "msaAppId": msa_app_id,
            "msaAppTenantId": tenant_id,
            "msaAppType": "SingleTenant",
        },
    }

    print(f"  Creating Bot Service '{bot_name}'...")
    result = _arm_rest("PUT", bot_url, bot_body)
    if not result:
        print("  ERROR: Failed to create Bot Service")
        return
    print("  Bot Service created")
    azd_env_set("AGENT_BOT_NAME", bot_name)

    # Create Teams Channel
    teams_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.BotService/botServices/{bot_name}"
        f"/channels/MsTeamsChannel"
        f"?api-version=2023-09-15-preview"
    )
    teams_body = {
        "location": "global",
        "properties": {
            "channelName": "MsTeamsChannel",
            "properties": {
                "isEnabled": True,
                "deploymentEnvironment": "CommercialDeployment",
            },
        },
    }
    print("  Creating Teams Channel...")
    result = _arm_rest("PUT", teams_url, teams_body)
    if result:
        print("  Teams Channel created")
    else:
        print("  WARNING: Failed to create Teams Channel")

    # Create DirectLine Channel
    dl_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.BotService/botServices/{bot_name}"
        f"/channels/DirectLineChannel"
        f"?api-version=2023-09-15-preview"
    )
    dl_body = {
        "location": "global",
        "properties": {
            "channelName": "DirectLineChannel",
            "properties": {
                "isEnabled": True,
                "sites": [
                    {
                        "siteName": "Default Site",
                        "isEnabled": True,
                        "isV1Enabled": True,
                        "isV3Enabled": True,
                    }
                ],
            },
        },
    }
    print("  Creating DirectLine Channel...")
    result = _arm_rest("PUT", dl_url, dl_body)
    if result:
        print("  DirectLine Channel created")
    else:
        print("  WARNING: Failed to create DirectLine Channel")


def publish_teams_app_org_wide(msa_app_id):
    """Generate Teams app manifest and publish to org catalog via Graph API.

    - Generates manifest.json with bot capability
    - Packages as ZIP with icons
    - POST /appCatalogs/teamsApps (or PUT to update existing)
    - requiresReview=false for instant availability
    """
    import zipfile

    env_name = os.environ.get("AZURE_ENV_NAME", "default")
    developer_name = os.environ.get("TEAMS_APP_DEVELOPER_NAME", "")
    privacy_url = os.environ.get("TEAMS_APP_PRIVACY_URL", "")
    terms_url = os.environ.get("TEAMS_APP_TERMS_URL", "")

    if not developer_name or not privacy_url or not terms_url:
        print("  Skipping Teams org catalog publish — missing required env vars:")
        if not developer_name:
            print("    azd env set TEAMS_APP_DEVELOPER_NAME <company-name>")
        if not privacy_url:
            print("    azd env set TEAMS_APP_PRIVACY_URL <url>")
        if not terms_url:
            print("    azd env set TEAMS_APP_TERMS_URL <url>")
        return

    # Deterministic app ID based on environment name
    app_external_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"salesforce-assistant-{env_name}"))

    manifest = {
        "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.19/MicrosoftTeams.schema.json",
        "manifestVersion": "1.19",
        "version": "1.0.0",
        "id": app_external_id,
        "developer": {
            "name": developer_name,
            "websiteUrl": privacy_url,
            "privacyUrl": privacy_url,
            "termsOfUseUrl": terms_url,
        },
        "name": {
            "short": "Salesforce Assistant",
            "full": f"Salesforce Assistant ({env_name})",
        },
        "description": {
            "short": "AI assistant with Salesforce access",
            "full": "AI-powered assistant that can query and update Salesforce on your behalf using natural language.",
        },
        "icons": {
            "color": "color.png",
            "outline": "outline.png",
        },
        "accentColor": "#0078D4",
        "bots": [
            {
                "botId": msa_app_id,
                "scopes": ["personal", "team", "groupChat"],
                "supportsFiles": False,
                "isNotificationOnly": False,
                "commandLists": [],
            }
        ],
        "permissions": ["identity", "messageTeamMembers"],
        "validDomains": [],
    }

    # Build ZIP package
    assets_dir = os.path.join(os.getcwd(), "assets", "teams")
    zip_path = os.path.join(tempfile.gettempdir(), "teams-app.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        color_path = os.path.join(assets_dir, "color.png")
        outline_path = os.path.join(assets_dir, "outline.png")
        if os.path.exists(color_path):
            zf.write(color_path, "color.png")
        if os.path.exists(outline_path):
            zf.write(outline_path, "outline.png")

    print(f"  Teams app package created: {zip_path}")

    # Check if app already in org catalog
    filter_query = f"externalId eq '{app_external_id}'"
    check_result = run(
        f'az rest --method GET '
        f'--url "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps'
        f'?$filter={filter_query}" '
        f'--headers "Content-Type=application/json"',
        parse_json=True,
    )

    existing_app_id = None
    if check_result and isinstance(check_result, dict):
        apps = check_result.get("value", [])
        if apps:
            existing_app_id = apps[0].get("id")

    if existing_app_id:
        # Update existing app
        print(f"  Updating existing Teams app (id: {existing_app_id})...")
        result = run(
            f'az rest --method PUT '
            f'--url "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/{existing_app_id}/appDefinitions" '
            f'--headers "Content-Type=application/zip" '
            f'--body "@{zip_path}"',
            parse_json=True,
        )
        if result:
            print("  Teams app updated in org catalog")
        else:
            print("  WARNING: Failed to update Teams app (may need AppCatalog.ReadWrite.All permission)")
    else:
        # Create new app in org catalog
        print("  Publishing Teams app to org catalog...")
        result = run(
            f'az rest --method POST '
            f'--url "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps?requiresReview=false" '
            f'--headers "Content-Type=application/zip" '
            f'--body "@{zip_path}"',
            parse_json=True,
        )
        if result:
            new_id = result.get("id", "unknown")
            print(f"  Teams app published to org catalog (id: {new_id})")
        else:
            print("  WARNING: Failed to publish Teams app (may need AppCatalog.ReadWrite.All permission)")

    # Clean up
    try:
        os.unlink(zip_path)
    except OSError:
        pass


def main():
    print("=== Post-provision hook (OBO) ===\n")

    # Step 0: Upload cert to Key Vault + configure APIM cert binding
    print("--- Step 0: Certificate upload + APIM binding ---")
    try:
        upload_cert_and_configure_apim()
    except Exception as e:
        print(f"\nWARNING: Certificate upload failed (non-fatal): {e}")
        traceback.print_exc()

    # Step 1: Create Chat App Entra registration
    print("\n--- Step 1: Chat App Entra registration ---")
    try:
        create_chat_app_entra_registration()
    except Exception as e:
        print(f"\nWARNING: Chat App Entra registration failed (non-fatal): {e}")
        traceback.print_exc()

    # Step 2: Create Foundry agent
    print("\n--- Step 2: Create Foundry agent ---")
    agent_version = None
    try:
        agent_version = create_agent()
    except Exception as e:
        print(f"\nWARNING: Agent creation failed (non-fatal): {e}")
        print("Re-run with: python hooks/postprovision.py")
        traceback.print_exc()

    # Step 3: Update Chat App env vars
    print("\n--- Step 3: Update Chat App settings ---")
    try:
        update_chat_app_settings()
    except Exception as e:
        print(f"\nWARNING: Chat App settings update failed (non-fatal): {e}")
        traceback.print_exc()

    # Step 4: Recreate OBO connection + update APIM Named Values
    print("\n--- Step 4: Salesforce OBO connection ---")
    try:
        update_obo_connection()
    except Exception as e:
        print(f"\nWARNING: SF OBO connection update failed (non-fatal): {e}")
        traceback.print_exc()

    print("\n--- Step 4b: OBO APIM Named Values ---")
    try:
        update_obo_apim_named_values()
    except Exception as e:
        print(f"\nWARNING: OBO APIM Named Values update failed (non-fatal): {e}")
        traceback.print_exc()

    # Step 5: Agent Application (REST-only, can't be Bicep)
    print("\n--- Step 5: Agent Application ---")
    msa_app_id = None
    try:
        msa_app_id = create_agent_application()
    except Exception as e:
        print(f"\nWARNING: Agent Application creation failed (non-fatal): {e}")
        traceback.print_exc()

    # Step 6: Agent Deployment (REST-only, needs agent version)
    if agent_version:
        print("\n--- Step 6: Agent Deployment ---")
        try:
            create_agent_deployment(agent_version)
        except Exception as e:
            print(f"\nWARNING: Agent Deployment failed (non-fatal): {e}")
            traceback.print_exc()
    else:
        print("\n--- Step 6: Agent Deployment (skipped — no agent version) ---")

    # Step 6b: Create Customer 360 agent (dual MCP — SF + SN)
    print("\n--- Step 6b: Create Customer 360 agent ---")
    c360_version = None
    try:
        c360_version = create_customer360_agent()
    except Exception as e:
        print(f"\nWARNING: Customer 360 agent creation failed (non-fatal): {e}")
        traceback.print_exc()

    # Step 6c: Customer 360 Agent Application
    if c360_version:
        print("\n--- Step 6c: Customer 360 Agent Application ---")
        try:
            create_customer360_application()
        except Exception as e:
            print(f"\nWARNING: Customer 360 Agent Application failed (non-fatal): {e}")
            traceback.print_exc()
    else:
        print("\n--- Step 6c: Customer 360 Agent Application (skipped — no agent version) ---")

    # Step 6d: Customer 360 Agent Deployment
    if c360_version:
        print("\n--- Step 6d: Customer 360 Agent Deployment ---")
        try:
            create_customer360_deployment(c360_version)
        except Exception as e:
            print(f"\nWARNING: Customer 360 Agent Deployment failed (non-fatal): {e}")
            traceback.print_exc()
    else:
        print("\n--- Step 6d: Customer 360 Agent Deployment (skipped — no agent version) ---")

    # Step 7: Bot Service bootstrap (first-run only, then Bicep takes over)
    if msa_app_id:
        print("\n--- Step 7: Bot Service bootstrap ---")
        try:
            create_bot_service_and_channels(msa_app_id)
        except Exception as e:
            print(f"\nWARNING: Bot Service bootstrap failed (non-fatal): {e}")
            traceback.print_exc()
    else:
        print("\n--- Step 7: Bot Service bootstrap (skipped — no msaAppId) ---")

    # Step 8: Teams org-wide distribution (Graph API)
    if msa_app_id:
        print("\n--- Step 8: Teams org catalog publish ---")
        try:
            publish_teams_app_org_wide(msa_app_id)
        except Exception as e:
            print(f"\nWARNING: Teams publish failed (non-fatal): {e}")
            traceback.print_exc()
    else:
        print("\n--- Step 8: Teams org catalog publish (skipped — no msaAppId) ---")

    print("\n=== Post-provision hook complete ===")


if __name__ == "__main__":
    main()
