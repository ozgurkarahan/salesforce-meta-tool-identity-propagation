"""Post-provision hook: create Entra app + configure auth + create Foundry agent.

After Bicep deploys Azure resources, this hook:
1. Creates Chat App Entra app registration (az CLI — delegated permissions)
2. Creates the Foundry agent with Salesforce MCP tool
3. Updates Chat App Container App env vars
4. Configures auth-mode-specific resources:
   - oauth2 (default): Recreates SF OAuth connection via DELETE+PUT (ApiHub)
   - obo: Updates APIM Named Values for JWT Bearer token exchange
5. Updates APIM SfInstanceUrl Named Value

SF_AUTH_MODE env var controls the auth flow:
- "oauth2" (default): User authenticates to both Azure AD and Salesforce (PKCE consent)
- "obo": User authenticates to Azure AD only; APIM exchanges for SF token (JWT Bearer)

Uses az CLI for Entra ops because the Graph Bicep extension requires
Application.ReadWrite.All on the ARM deployment identity, which is not
available in managed tenants.

Uses azure-ai-projects v2 SDK for Foundry agent (no ARM resource type).
"""

import json
import os
import subprocess
import sys
import tempfile
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

    # Connection config env vars — needed by /api/reset-mcp-auth endpoint
    sub_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    account = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project_name = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")
    apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
    sf_client_id = os.environ.get("SF_CONNECTED_APP_CLIENT_ID", "")
    sf_client_secret = os.environ.get("SF_CONNECTED_APP_CLIENT_SECRET", "")
    sf_instance_url = os.environ.get("SF_INSTANCE_URL", "")

    print(f"  Updating {chat_app_name} environment variables...")
    result = run(
        f'az containerapp update --name {chat_app_name} --resource-group {rg} '
        f'--set-env-vars '
        f'"CHAT_APP_ENTRA_CLIENT_ID={client_id}" '
        f'"TENANT_ID={tenant_id}" '
        f'"AGENT_NAME={agent_name}" '
        f'"AZURE_SUBSCRIPTION_ID={sub_id}" '
        f'"AZURE_RESOURCE_GROUP={rg}" '
        f'"COGNITIVE_ACCOUNT_NAME={account}" '
        f'"AI_FOUNDRY_PROJECT_NAME={project_name}" '
        f'"APIM_GATEWAY_URL={apim_gateway}" '
        f'"SF_CONNECTED_APP_CLIENT_ID={sf_client_id}" '
        f'"SF_CONNECTED_APP_CLIENT_SECRET={sf_client_secret}" '
        f'"SF_INSTANCE_URL={sf_instance_url}"',
    )
    if result is not None:
        print("  Container App env vars updated")
    else:
        print("  WARNING: Failed to update Container App env vars")


def update_sf_oauth_connection():
    """Recreate the Salesforce OAuth connection with real credentials via ARM REST.

    Bicep-created connections do NOT register the ApiHub connector that Foundry
    needs for interactive OAuth consent. The fix is to DELETE the Bicep-created
    connection and PUT a fresh one via ARM REST, which triggers ApiHub setup.

    Reads SF_CONNECTED_APP_CLIENT_ID and SF_CONNECTED_APP_CLIENT_SECRET from env.
    Skips gracefully if not set.
    """
    client_id = os.environ.get("SF_CONNECTED_APP_CLIENT_ID", "")
    client_secret = os.environ.get("SF_CONNECTED_APP_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("  Skipping — SF_CONNECTED_APP_CLIENT_ID or SF_CONNECTED_APP_CLIENT_SECRET not set")
        print("  Set them with: azd env set SF_CONNECTED_APP_CLIENT_ID <consumer-key>")
        print("                 azd env set SF_CONNECTED_APP_CLIENT_SECRET <consumer-secret>")
        return

    connection_name = os.environ.get("SF_OAUTH_CONNECTION_NAME", "salesforce-oauth")
    sf_mcp_endpoint = os.environ.get("APIM_SF_MCP_ENDPOINT", "")

    if not sf_mcp_endpoint:
        apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
        if apim_gateway:
            sf_mcp_endpoint = f"{apim_gateway}/salesforce-mcp/mcp"
    if not sf_mcp_endpoint:
        print("  WARNING: No SF MCP endpoint — skipping connection update")
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

    # Step 1: Delete the Bicep-created connection
    print(f"  Deleting Bicep-created connection '{connection_name}'...")
    run(f'az rest --method DELETE --url "{url}"')

    # Use My Domain URL for OAuth so consent routes through Azure AD SSO.
    sf_login_url = (
        os.environ.get("SF_LOGIN_URL")
        or os.environ.get("SF_INSTANCE_URL")
        or "https://login.salesforce.com"
    )

    # Step 2: Recreate via ARM REST PUT (triggers ApiHub connector registration)
    body = {
        "properties": {
            "authType": "OAuth2",
            "category": "RemoteTool",
            "group": "GenericProtocol",
            "connectorName": connection_name,
            "target": sf_mcp_endpoint,
            "credentials": {
                "clientId": client_id,
                "clientSecret": client_secret,
            },
            "authorizationUrl": f"{sf_login_url}/services/oauth2/authorize",
            "tokenUrl": f"{sf_login_url}/services/oauth2/token",
            "refreshUrl": f"{sf_login_url}/services/oauth2/token",
            "scopes": ["api", "refresh_token"],
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
            print("  SF OAuth connection created (ApiHub connector registered)")
            # Print ApiHub redirect URI for SF Connected App callback
            _print_sf_apihub_redirect_uri(connection_name)
        else:
            print("  WARNING: Failed to create SF OAuth connection")
    finally:
        os.unlink(body_file)


def _print_sf_apihub_redirect_uri(connection_name: str):
    """Print the ApiHub redirect URI so the user can add it to SF Connected App."""
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    account = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project_name = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")

    if not all([sub_id, rg, account, project_name]):
        return

    project_url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        f"/projects/{project_name}?api-version=2025-04-01-preview"
    )
    project_props = run(
        f'az rest --method GET --url "{project_url}" '
        f'--query "properties.internalId" -o tsv',
    )
    if project_props:
        iid = project_props.strip()
        guid = f"{iid[:8]}-{iid[8:12]}-{iid[12:16]}-{iid[16:20]}-{iid[20:]}"
        connector_id = f"{guid}-{connection_name}"
        redirect_uri = f"https://global.consent.azure-apim.net/redirect/{connector_id}"
        print(f"\n  ** Add this redirect URI to your SF Connected App callback URLs: **")
        print(f"  {redirect_uri}\n")


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


def update_sf_apim_named_value():
    """Update the APIM SfInstanceUrl Named Value with the real org instance URL.

    SF JWT tokens use org-specific issuer/audience (the instance URL, not
    login.salesforce.com). Bicep deploys a placeholder; this patches it.
    """
    sf_instance_url = os.environ.get("SF_INSTANCE_URL", "")
    if not sf_instance_url:
        print("  Skipping — SF_INSTANCE_URL not set")
        return

    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    apim_name = os.environ.get("APIM_NAME", "")

    if not sub_id:
        print("  WARNING: Could not get subscription ID — skipping")
        return

    url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}"
        f"/namedValues/SfInstanceUrl"
        f"?api-version=2024-06-01-preview"
    )

    body = {
        "properties": {
            "displayName": "SfInstanceUrl",
            "value": sf_instance_url,
            "secret": False,
        }
    }

    body_file = _write_temp_json(body)
    try:
        print(f"  Updating APIM Named Value 'SfInstanceUrl' = {sf_instance_url}...")
        result = run(
            f'az rest --method PUT --url "{url}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
        if result:
            print("  Named Value updated successfully")
        else:
            print("  WARNING: Failed to update Named Value")
    finally:
        os.unlink(body_file)


def create_agent():
    """Create a Foundry agent with the Salesforce MCP tool using the v2 SDK.

    In OBO mode, uses the OBO connection (AAD auth) and the OBO APIM endpoint.
    In OAuth2 mode, uses the OAuth connection (ApiHub consent) and the standard endpoint.
    """
    auth_mode = os.environ.get("SF_AUTH_MODE", "oauth2")
    project_endpoint = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT")

    if not project_endpoint:
        print("WARNING: Missing AI_FOUNDRY_PROJECT_ENDPOINT — skipping agent creation.")
        return

    # Select endpoint and connection based on auth mode
    if auth_mode == "obo":
        sf_mcp_endpoint = os.environ.get("APIM_SF_MCP_OBO_ENDPOINT", "")
        if not sf_mcp_endpoint:
            apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
            if apim_gateway:
                sf_mcp_endpoint = f"{apim_gateway}/salesforce-mcp-obo/mcp"
        connection_name = os.environ.get("SF_OBO_CONNECTION_NAME", "salesforce-obo")
    else:
        sf_mcp_endpoint = os.environ.get("APIM_SF_MCP_ENDPOINT", "")
        if not sf_mcp_endpoint:
            apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
            if apim_gateway:
                sf_mcp_endpoint = f"{apim_gateway}/salesforce-mcp/mcp"
        connection_name = os.environ.get("SF_OAUTH_CONNECTION_NAME", "")

    if not sf_mcp_endpoint:
        print("WARNING: No SF MCP endpoint available — skipping agent creation.")
        return

    print(f"\nAuth mode:        {auth_mode}")
    print(f"Project endpoint: {project_endpoint}")
    print(f"SF MCP endpoint:  {sf_mcp_endpoint}")
    print(f"Connection:       {connection_name or '(none)'}")

    from azure.identity import DefaultAzureCredential
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition, MCPTool

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

    instructions = (
        "You are an assistant with access to Salesforce. "
        "Use the Salesforce MCP tools to query Salesforce data — "
        "list objects, describe fields, run SOQL queries, search records, "
        "write records, and process approvals. "
        "Always confirm destructive actions with the user."
    )

    agent = project_client.agents.create_version(
        agent_name=agent_name,
        definition=PromptAgentDefinition(
            model="gpt-4o",
            instructions=instructions,
            tools=tools,
        ),
    )
    print(f"Agent created: name={agent.name}, version={agent.version}, id={agent.id}")
    print(f"  Tools: {len(tools)} MCP tool(s) configured")
    print(f"  Auth mode: {auth_mode}")

    # --- Smoke test ---
    if auth_mode == "obo":
        print("\n--- Smoke test skipped (OBO mode) ---")
        print("OBO flow requires no consent. Send a chat message to test.")
    elif connection_name:
        print("\n--- Smoke test skipped (OAuth configured) ---")
        print("Run the interactive OAuth test to verify end-to-end:")
        print("  python scripts/test-agent-oauth.py")
    else:
        print("\n--- Smoke test skipped (no connection) ---")
        print("Set SF credentials and re-run to enable auth flow.")

    print(f"\nAgent: {agent.name} v{agent.version}")


def main():
    auth_mode = os.environ.get("SF_AUTH_MODE", "oauth2")
    print(f"=== Post-provision hook (SF_AUTH_MODE={auth_mode}) ===\n")

    # Step 1: Create Chat App Entra registration (both modes)
    print("--- Step 1: Chat App Entra registration ---")
    try:
        create_chat_app_entra_registration()
    except Exception as e:
        print(f"\nWARNING: Chat App Entra registration failed (non-fatal): {e}")
        traceback.print_exc()

    # Step 2: Create Foundry agent (both modes — uses auth-mode-aware connection)
    print("\n--- Step 2: Create Foundry agent ---")
    try:
        create_agent()
    except Exception as e:
        print(f"\nWARNING: Agent creation failed (non-fatal): {e}")
        print("Re-run with: python hooks/postprovision.py")
        traceback.print_exc()

    # Step 3: Update Chat App env vars (both modes)
    print("\n--- Step 3: Update Chat App settings ---")
    try:
        update_chat_app_settings()
    except Exception as e:
        print(f"\nWARNING: Chat App settings update failed (non-fatal): {e}")
        traceback.print_exc()

    # Step 4: Auth-mode-specific connection setup
    if auth_mode == "obo":
        # OBO: Recreate OBO connection + update APIM named values for JWT Bearer
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
    else:
        # OAuth2: Recreate OAuth connection (triggers ApiHub connector registration)
        print("\n--- Step 4: Salesforce OAuth connection ---")
        try:
            update_sf_oauth_connection()
        except Exception as e:
            print(f"\nWARNING: SF OAuth connection update failed (non-fatal): {e}")
            traceback.print_exc()

    # Step 5: Update APIM SfInstanceUrl Named Value (both modes)
    print("\n--- Step 5: Update APIM SfInstanceUrl Named Value ---")
    try:
        update_sf_apim_named_value()
    except Exception as e:
        print(f"\nWARNING: SF APIM Named Value update failed (non-fatal): {e}")
        traceback.print_exc()

    print(f"\n=== Post-provision hook complete (mode: {auth_mode}) ===")


if __name__ == "__main__":
    main()
