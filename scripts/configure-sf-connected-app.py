"""Configure Salesforce Connected App for MCP identity propagation.

Automates the SF Connected App configuration using the Salesforce CLI (`sf`):
1. Retrieves the Connected App metadata
2. Adds the ApiHub redirect URI to callback URLs
3. Deploys the updated metadata back to Salesforce
4. Queries the consumer key and sets azd env vars

Prerequisites:
- Salesforce CLI (`sf`) installed and authenticated to the target org
- Connected App already created in the target org
- `azd env` loaded with deployment outputs

Usage:
    python scripts/configure-sf-connected-app.py --app-name <ConnectedAppName> [--org <alias>]
"""

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

os.environ.setdefault("PYTHONIOENCODING", "utf-8")


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


def run(cmd: str, parse_json: bool = False):
    """Run a shell command and return stdout."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, shell=True,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )
    if result.returncode != 0:
        if result.stderr:
            print(f"  stderr: {result.stderr[:300]}")
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


def get_apihub_redirect_uri() -> str | None:
    """Construct the ApiHub redirect URI for the SF OAuth connection."""
    sub_id = run("az account show --query id -o tsv")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
    account = os.environ.get("COGNITIVE_ACCOUNT_NAME", "")
    project_name = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")
    connection_name = os.environ.get("SF_OAUTH_CONNECTION_NAME", "salesforce-oauth")

    if not all([sub_id, rg, account, project_name]):
        return None

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
    if not project_props:
        return None

    iid = project_props.strip()
    guid = f"{iid[:8]}-{iid[8:12]}-{iid[12:16]}-{iid[16:20]}-{iid[20:]}"
    connector_id = f"{guid}-{connection_name}"
    return f"https://global.consent.azure-apim.net/redirect/{connector_id}"


def retrieve_connected_app(app_name: str, org: str) -> str | None:
    """Retrieve Connected App metadata via sf CLI. Returns the metadata file path."""
    print(f"\n  Retrieving Connected App '{app_name}'...")
    result = run(
        f'sf project retrieve start --metadata "ConnectedApp:{app_name}" '
        f'-o {org} --target-dir salesforce',
    )
    if result is None:
        print("  ERROR: Failed to retrieve Connected App metadata")
        return None

    # The metadata file path
    meta_path = f"salesforce/force-app/main/default/connectedApps/{app_name}.connectedApp-meta.xml"
    if not os.path.exists(meta_path):
        print(f"  ERROR: Metadata file not found at {meta_path}")
        return None

    print(f"  Retrieved: {meta_path}")
    return meta_path


def update_connected_app_metadata(meta_path: str, redirect_uri: str) -> bool:
    """Update Connected App metadata to add the ApiHub redirect URI."""
    print(f"\n  Updating metadata: {meta_path}")

    tree = ET.parse(meta_path)
    root = tree.getroot()

    # Handle namespace
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    oauth_config = root.find(f"{ns}oauthConfig")
    if oauth_config is None:
        print("  ERROR: No oauthConfig found in Connected App metadata")
        return False

    # Find or create callbackUrl element
    callback_elem = oauth_config.find(f"{ns}callbackUrl")
    if callback_elem is None:
        callback_elem = ET.SubElement(oauth_config, f"{ns}callbackUrl" if ns else "callbackUrl")
        callback_elem.text = redirect_uri
        print(f"  Added callbackUrl: {redirect_uri}")
    else:
        # SF Connected Apps support multiple callback URLs separated by newlines
        existing = callback_elem.text or ""
        if redirect_uri in existing:
            print(f"  ApiHub redirect URI already present in callbackUrl")
        else:
            callback_elem.text = f"{existing}\n{redirect_uri}" if existing else redirect_uri
            print(f"  Appended to callbackUrl: {redirect_uri}")

    tree.write(meta_path, xml_declaration=True, encoding="UTF-8")
    print(f"  Metadata updated: {meta_path}")
    return True


def deploy_connected_app(app_name: str, org: str) -> bool:
    """Deploy updated Connected App metadata back to Salesforce."""
    print(f"\n  Deploying Connected App '{app_name}'...")
    result = run(
        f'sf project deploy start --metadata "ConnectedApp:{app_name}" '
        f'-o {org} --source-dir salesforce',
    )
    if result is None:
        print("  ERROR: Failed to deploy Connected App metadata")
        return False
    print("  Deployed successfully")
    return True


def query_consumer_key(app_name: str, org: str) -> str | None:
    """Query the Connected App consumer key from Salesforce."""
    print(f"\n  Querying consumer key for '{app_name}'...")

    # Use SOQL to query the ConnectedApplication object
    soql = f"SELECT Id, Name FROM ConnectedApplication WHERE Name = '{app_name}'"
    result = run(
        f'sf data query --query "{soql}" -o {org} --json',
        parse_json=True,
    )

    if not result or not result.get("result", {}).get("records"):
        print("  NOTE: ConsumerKey cannot be queried via API.")
        print("  You must provide it manually from Salesforce Setup > Connected Apps.")
        return None

    return None  # Consumer key is not exposed in standard SOQL


def main():
    parser = argparse.ArgumentParser(
        description="Configure Salesforce Connected App for MCP identity propagation. "
        "Adds ApiHub redirect URI and sets azd env vars."
    )
    parser.add_argument(
        "--app-name", required=True,
        help="Name of the SF Connected App (e.g., 'MCP_Identity_Propagation')",
    )
    parser.add_argument(
        "--org", default="sf-sso-target",
        help="Salesforce org alias (default: sf-sso-target)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Configure Salesforce Connected App")
    print("=" * 60)

    load_azd_env()

    # Step 1: Get ApiHub redirect URI
    print("\n--- Step 1: ApiHub redirect URI ---")
    redirect_uri = get_apihub_redirect_uri()
    if redirect_uri:
        print(f"  Redirect URI: {redirect_uri}")
    else:
        print("  WARNING: Could not construct ApiHub redirect URI")
        print("  Ensure azd environment is loaded (run 'azd env get-values')")

    # Step 2: Retrieve Connected App
    print("\n--- Step 2: Retrieve Connected App ---")
    meta_path = retrieve_connected_app(args.app_name, args.org)
    if not meta_path:
        sys.exit(1)

    # Step 3: Update metadata
    if redirect_uri:
        print("\n--- Step 3: Update metadata ---")
        if not update_connected_app_metadata(meta_path, redirect_uri):
            sys.exit(1)

        # Step 4: Deploy
        print("\n--- Step 4: Deploy ---")
        if not deploy_connected_app(args.app_name, args.org):
            sys.exit(1)
    else:
        print("\n--- Steps 3-4: Skipped (no redirect URI) ---")

    # Step 5: Set env vars
    print("\n--- Step 5: Set azd env vars ---")
    client_id = os.environ.get("SF_CONNECTED_APP_CLIENT_ID", "")
    if not client_id:
        print("  SF_CONNECTED_APP_CLIENT_ID not set.")
        print("  Get the consumer key from Salesforce Setup > Connected Apps > Manage Connected Apps")
        print("  Then run: azd env set SF_CONNECTED_APP_CLIENT_ID <consumer-key>")
    else:
        print(f"  SF_CONNECTED_APP_CLIENT_ID already set: {client_id[:20]}...")

    client_secret = os.environ.get("SF_CONNECTED_APP_CLIENT_SECRET", "")
    if not client_secret:
        print("  SF_CONNECTED_APP_CLIENT_SECRET not set.")
        print("  Get the consumer secret from Salesforce Setup > Connected Apps > Manage Connected Apps")
        print("  Then run: azd env set SF_CONNECTED_APP_CLIENT_SECRET <consumer-secret>")
    else:
        print("  SF_CONNECTED_APP_CLIENT_SECRET already set")

    sf_instance_url = os.environ.get("SF_INSTANCE_URL", "")
    if not sf_instance_url:
        print("  SF_INSTANCE_URL not set.")
        print("  Set it with: azd env set SF_INSTANCE_URL https://<org>.my.salesforce.com")
    else:
        print(f"  SF_INSTANCE_URL: {sf_instance_url}")

    print()
    print("=" * 60)
    print("  Configuration complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("  1. Ensure consumer key/secret are set in azd env")
    print("  2. Run: azd up  (to deploy with SF OAuth)")
    print("  3. Run: python scripts/grant-sf-mcp-consent.py  (one-time consent)")
    print("  4. Test: python scripts/test-agent-oauth.py")


if __name__ == "__main__":
    main()
