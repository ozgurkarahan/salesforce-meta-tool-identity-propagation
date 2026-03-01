"""Grant OAuth consent for the Salesforce MCP connection.

This script completes the Salesforce OAuth authorization code flow with PKCE
that populates the Foundry connection with a user's SF refresh token, enabling
the agent to acquire delegated SF OAuth tokens when calling Salesforce MCP tools.

One-time manual step after `azd up` with SF OAuth configured.

Flow:
1. Load azd env vars (SF_CONNECTED_APP_CLIENT_ID, etc.)
2. Salesforce OAuth authorization code flow with PKCE (opens browser)
3. PUT to CognitiveServices connection to store the refresh token
4. Print success + next steps

Usage: python scripts/grant-sf-mcp-consent.py
"""

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import urllib.error
import webbrowser

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

REDIRECT_URI = "http://localhost:8444/callback"


def load_azd_env():
    """Load azd env vars into os.environ."""
    result = subprocess.run(
        "azd env get-values", capture_output=True, text=True, shell=True,
    )
    if result.returncode != 0:
        return
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if "=" in line:
            key, _, value = line.partition("=")
            value = value.strip('"').strip("'")
            os.environ.setdefault(key, value)


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256).

    Returns (code_verifier, code_challenge).
    - code_verifier: 128-char random string from unreserved characters
    - code_challenge: base64url(SHA256(code_verifier)), no padding
    """
    # 96 random bytes -> 128 base64url chars (within the 43-128 char spec)
    code_verifier = secrets.token_urlsafe(96)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def sf_auth_code_flow(client_id: str, client_secret: str, login_url: str) -> dict:
    """Run Salesforce OAuth authorization code flow with PKCE (opens browser).

    Uses PKCE (S256) to match what ApiHub does. The SF Connected App must have
    "Require Proof Key for Code Exchange (PKCE)" enabled so both sides validate
    the code_verifier/code_challenge handshake end-to-end.
    """
    code_verifier, code_challenge = _generate_pkce_pair()
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in params:
                result["code"] = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>Success! You can close this tab.</h2>")
            else:
                result["error"] = params.get("error", ["unknown"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"<h2>Error: {result['error']}</h2>".encode())

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("localhost", 8444), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    authorize_url = (
        f"{login_url}/services/oauth2/authorize"
        f"?response_type=code"
        f"&client_id={urllib.parse.quote(client_id)}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&scope=api+refresh_token"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )

    print("Opening browser for Salesforce login (with PKCE)...")
    print(f"  URL: {authorize_url[:100]}...")
    try:
        webbrowser.open(authorize_url)
    except Exception:
        print(f"\n  Please open this URL in your browser:\n  {authorize_url}")

    thread.join(timeout=120)
    server.server_close()

    if "error" in result:
        print(f"\nAuthorization failed: {result['error']}")
        return {}
    if "code" not in result:
        print("\nTimed out waiting for authorization callback")
        return {}

    # Exchange code for tokens (include code_verifier for PKCE)
    print("\nExchanging authorization code for tokens (with PKCE code_verifier)...")
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": result["code"],
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
    }).encode()

    req = urllib.request.Request(
        f"{login_url}/services/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        resp = json.loads(urllib.request.urlopen(req).read())
        print("Token exchange successful!")
        return resp
    except urllib.error.HTTPError as e:
        error = e.read().decode()
        print(f"Token exchange failed: {e.code}")
        print(error[:500])
        return {}


def get_arm_token() -> str:
    """Get ARM management token via DefaultAzureCredential."""
    from azure.identity import DefaultAzureCredential
    credential = DefaultAzureCredential()
    token = credential.get_token("https://management.azure.com/.default")
    return token.token


def update_connection(
    arm_token: str,
    connection_name: str,
    client_id: str,
    client_secret: str,
    sf_mcp_endpoint: str,
    refresh_token: str,
) -> bool:
    """Update the Foundry connection with the SF refresh token via ARM REST."""
    sub_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    if not sub_id:
        account = subprocess.run(
            "az account show --query id -o tsv",
            capture_output=True, text=True, shell=True,
        )
        sub_id = account.stdout.strip()

    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    account_name = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project_name = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")

    url = (
        f"https://management.azure.com/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/projects/{project_name}/connections/{connection_name}"
        f"?api-version=2025-04-01-preview"
    )

    body = json.dumps({
        "properties": {
            "authType": "OAuth2",
            "category": "RemoteTool",
            "group": "GenericProtocol",
            "connectorName": connection_name,
            "target": sf_mcp_endpoint,
            "credentials": {
                "clientId": client_id,
                "clientSecret": client_secret,
                "refreshToken": refresh_token,
            },
            "authorizationUrl": f"{login_url}/services/oauth2/authorize",
            "tokenUrl": f"{login_url}/services/oauth2/token",
            "refreshUrl": f"{login_url}/services/oauth2/token",
            "scopes": ["api", "refresh_token"],
            "metadata": {"type": "custom_MCP"},
            "isSharedToAll": True,
        }
    }).encode()

    # DELETE first, then PUT — required to register ApiHub connector reliably.
    # A plain PUT can leave the connector deregistered, causing
    # "Failed to create ApiHub connection: Not Found" at runtime.
    del_req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {arm_token}"},
        method="DELETE",
    )
    try:
        urllib.request.urlopen(del_req)
        print(f"  Deleted existing connection '{connection_name}'")
    except urllib.error.HTTPError:
        print(f"  Connection '{connection_name}' did not exist (ok)")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {arm_token}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )

    try:
        resp = json.loads(urllib.request.urlopen(req).read())
        print(f"Connection '{connection_name}' created with SF refresh token.")
        props = resp.get("properties", {})
        print(f"  AuthType: {props.get('authType')}")
        print(f"  Target: {props.get('target')}")
        return True
    except urllib.error.HTTPError as e:
        error = e.read().decode()
        print(f"Failed to create connection: {e.code}")
        print(error[:500])
        return False


def main():
    print("=" * 60)
    print("  Grant Salesforce OAuth Consent for MCP Connection")
    print("=" * 60)
    print()

    load_azd_env()

    client_id = os.environ.get("SF_CONNECTED_APP_CLIENT_ID", "")
    client_secret = os.environ.get("SF_CONNECTED_APP_CLIENT_SECRET", "")
    connection_name = os.environ.get("SF_OAUTH_CONNECTION_NAME", "salesforce-oauth")
    sf_mcp_endpoint = os.environ.get("APIM_SF_MCP_ENDPOINT", "")
    login_url = (
        os.environ.get("SF_LOGIN_URL")
        or os.environ.get("SF_INSTANCE_URL")
        or "https://login.salesforce.com"
    )

    if not client_id or not client_secret:
        print("ERROR: Missing SF OAuth env vars.")
        print(f"  SF_CONNECTED_APP_CLIENT_ID:     {'set' if client_id else 'MISSING'}")
        print(f"  SF_CONNECTED_APP_CLIENT_SECRET: {'set' if client_secret else 'MISSING'}")
        print()
        print("Set them with:")
        print("  azd env set SF_CONNECTED_APP_CLIENT_ID <consumer-key>")
        print("  azd env set SF_CONNECTED_APP_CLIENT_SECRET <consumer-secret>")
        sys.exit(1)

    if not sf_mcp_endpoint:
        apim_gateway = os.environ.get("APIM_GATEWAY_URL", "")
        if apim_gateway:
            sf_mcp_endpoint = f"{apim_gateway}/salesforce-mcp"
        else:
            print("ERROR: Cannot determine SF MCP endpoint. Run 'azd up' first.")
            sys.exit(1)

    print(f"Connection:  {connection_name}")
    print(f"Client ID:   {client_id}")
    print(f"Login URL:   {login_url}")
    print(f"MCP target:  {sf_mcp_endpoint}")
    print()

    # Step 1: Salesforce auth code flow
    print(f"NOTE: Ensure {REDIRECT_URI} is in your SF Connected App callback URLs.")
    print()
    tokens = sf_auth_code_flow(client_id, client_secret, login_url)
    if not tokens:
        print("\nFailed to authenticate with Salesforce.")
        sys.exit(1)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("\nNo refresh token received. Ensure 'refresh_token' scope is configured.")
        sys.exit(1)

    print(f"\nGot SF refresh token (length: {len(refresh_token)})")

    # Step 2: Get ARM token
    print("\nGetting ARM management token...")
    arm_token = get_arm_token()
    print("Got ARM token.")

    # Step 3: Update connection with refresh token
    print(f"\nUpdating connection '{connection_name}'...")
    success = update_connection(
        arm_token=arm_token,
        connection_name=connection_name,
        client_id=client_id,
        client_secret=client_secret,
        sf_mcp_endpoint=sf_mcp_endpoint,
        refresh_token=refresh_token,
    )

    if success:
        print()
        print("=" * 60)
        print("  SF OAuth consent granted successfully!")
        print("  The Salesforce MCP connection can now acquire tokens.")
        print("=" * 60)
        print()
        print("Next steps:")
        print("  1. Test the agent: python scripts/test-agent-oauth.py")
    else:
        print("\nFailed to update connection.")
        sys.exit(1)


if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Grant Salesforce OAuth consent for the MCP connection. "
        "One-time manual step after 'azd up' to populate the connection with a SF refresh token."
    ).parse_args()
    main()
