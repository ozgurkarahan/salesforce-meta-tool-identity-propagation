"""Create Salesforce External Client App via Metadata API.

Automates the creation of an ExternalClientApplication (ECA) with OAuth settings:
1. Checks if the ECA already exists (via metadata retrieve)
2. Generates ECA metadata XML + OAuth settings metadata XML
3. Deploys via sf CLI
4. Prints manual steps for Consumer Key/Secret and PKCE

Note: PKCE cannot be enabled via Metadata API -- it must be enabled manually
in SF Setup after deployment. The script prints instructions for this.

Prerequisites:
- Salesforce CLI (sf) installed and authenticated to the target org
- azd env loaded (optional, for setting client ID automatically)

Usage:
    python scripts/setup-sf-external-client-app.py --org <alias> --email <admin-email>
    python scripts/setup-sf-external-client-app.py --org <alias> --email <admin-email> --app-name MyApp
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DEFAULT_APP_NAME = "Identity_PoC_MCP"
DEFAULT_APP_LABEL = "Identity PoC MCP"


def run(cmd: str, parse_json: bool = False, cwd: str | None = None):
    """Run a shell command and return stdout."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        cwd=cwd,
    )
    if result.returncode != 0:
        if result.stderr:
            print(f"  stderr: {result.stderr[:500]}")
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


def load_azd_env():
    """Load azd env vars into os.environ."""
    result = subprocess.run(
        "azd env get-values", capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if "=" in line:
            key, _, value = line.partition("=")
            value = value.strip('"').strip("'")
            os.environ.setdefault(key, value)


def _init_sfdx_project(work_dir: str):
    """Create a minimal sfdx-project.json + force-app dir so sf CLI commands work."""
    project_json = os.path.join(work_dir, "sfdx-project.json")
    if not os.path.exists(project_json):
        with open(project_json, "w") as f:
            json.dump({
                "packageDirectories": [{"path": "force-app", "default": True}],
                "namespace": "",
                "sfdcLoginUrl": "https://login.salesforce.com",
                "sourceApiVersion": "62.0",
            }, f, indent=2)
    os.makedirs(os.path.join(work_dir, "force-app", "main", "default"), exist_ok=True)


def check_eca_exists(org: str, app_name: str) -> bool:
    """Check if the ECA already exists by trying to retrieve its metadata."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as check_dir:
        _init_sfdx_project(check_dir)
        result = run(
            f'sf project retrieve start --metadata "ExternalClientApplication:{app_name}" '
            f"-o {org}",
            cwd=check_dir,
        )
        if result is None:
            return False
        # Check if the metadata file was actually created
        meta_path = os.path.join(
            check_dir, "force-app", "main", "default",
            "externalClientApps", f"{app_name}.eca-meta.xml",
        )
        return os.path.exists(meta_path)


def generate_eca_metadata(work_dir: str, app_name: str, app_label: str, email: str):
    """Generate ExternalClientApplication metadata XML.

    Uses the actual SFDX source format discovered from a real SF org:
    - Directory: externalClientApps/ (not externalClientApplications/)
    - Suffix: .eca-meta.xml (not .externalClientApplication-meta.xml)
    """
    _init_sfdx_project(work_dir)
    print(f"  Generating ECA metadata for '{app_label}'...")

    eca_xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<ExternalClientApplication xmlns="http://soap.sforce.com/2006/04/metadata">
    <contactEmail>{email}</contactEmail>
    <distributionState>Local</distributionState>
    <label>{app_label}</label>
</ExternalClientApplication>
"""
    eca_dir = os.path.join(
        work_dir, "force-app", "main", "default", "externalClientApps",
    )
    os.makedirs(eca_dir, exist_ok=True)
    eca_path = os.path.join(eca_dir, f"{app_name}.eca-meta.xml")
    with open(eca_path, "w", encoding="utf-8") as f:
        f.write(eca_xml)

    print(f"  Created: {os.path.relpath(eca_path, work_dir)}")
    return eca_path


def generate_oauth_settings(work_dir: str, app_name: str, callback_url: str | None = None):
    """Generate ExtlClntAppOauthSettings metadata XML.

    Uses the actual SFDX source format discovered from a real SF org:
    - Directory: extlClntAppOauthSettings/
    - Name: {AppName}_oauth
    - Suffix: .ecaOauth-meta.xml
    - Field: commaSeparatedOauthScopes (not commaSeparatedOAuth2Scopes)
    - Must include externalClientApplication and label fields

    Note: PKCE cannot be set via Metadata API. The isCodeCredentialFlowWithPKCE
    field is rejected by SF with "Element invalid at this location". PKCE must
    be enabled manually in SF Setup after deployment.

    The callback URL defaults to the standard SF OAuth callback; use
    configure-sf-connected-app.py to add the ApiHub redirect URI after Azure deployment.
    """
    print(f"  Generating OAuth settings for '{app_name}'...")

    cb = callback_url or "https://login.salesforce.com/services/oauth2/callback"
    oauth_name = f"{app_name}_oauth"

    oauth_xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppOauthSettings xmlns="http://soap.sforce.com/2006/04/metadata">
    <callbackUrl>{cb}</callbackUrl>
    <commaSeparatedOauthScopes>Api, RefreshToken</commaSeparatedOauthScopes>
    <externalClientApplication>{app_name}</externalClientApplication>
    <isFirstPartyAppEnabled>false</isFirstPartyAppEnabled>
    <label>{oauth_name}</label>
</ExtlClntAppOauthSettings>
"""
    oauth_dir = os.path.join(
        work_dir, "force-app", "main", "default", "extlClntAppOauthSettings",
    )
    os.makedirs(oauth_dir, exist_ok=True)
    oauth_path = os.path.join(oauth_dir, f"{oauth_name}.ecaOauth-meta.xml")
    with open(oauth_path, "w", encoding="utf-8") as f:
        f.write(oauth_xml)

    print(f"  Created: {os.path.relpath(oauth_path, work_dir)}")
    return oauth_path


def deploy_eca(org: str, work_dir: str) -> bool:
    """Deploy ECA + OAuth settings to Salesforce."""
    print("  Deploying ECA + OAuth settings...")
    result = run(
        f"sf project deploy start -o {org} --source-dir force-app",
        cwd=work_dir,
    )
    if result is None:
        print("  ERROR: Failed to deploy ECA metadata")
        print("  Verify the sf CLI is authenticated and the org is accessible:")
        print(f"    sf org display -o {org}")
        return False
    print("  Deployed successfully")
    return True


def set_azd_env(key: str, value: str) -> bool:
    """Set an azd env variable."""
    result = run(f'azd env set {key} "{value}"')
    return result is not None


def main():
    parser = argparse.ArgumentParser(
        description="Create Salesforce External Client App via Metadata API. "
        "Deploys ECA + OAuth settings, then prints manual steps for consumer "
        "key/secret and PKCE."
    )
    parser.add_argument(
        "--org", required=True,
        help="Salesforce org alias (as authenticated with 'sf org login web')",
    )
    parser.add_argument(
        "--email", required=True,
        help="Contact email for the External Client App",
    )
    parser.add_argument(
        "--app-name", default=DEFAULT_APP_NAME,
        help=f"ECA developer name (default: {DEFAULT_APP_NAME})",
    )
    parser.add_argument(
        "--app-label", default=DEFAULT_APP_LABEL,
        help=f"ECA display label (default: {DEFAULT_APP_LABEL})",
    )
    parser.add_argument(
        "--callback-url", default=None,
        help="OAuth callback URL (default: SF standard; add ApiHub URI later "
        "via configure-sf-connected-app.py)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Deploy even if the ECA already exists (overwrites OAuth settings)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Create Salesforce External Client App")
    print("=" * 60)

    load_azd_env()

    # Step 1: Check if ECA already exists
    print("\n--- Step 1: Check existing ECA ---")
    exists = check_eca_exists(args.org, args.app_name)

    if exists and not args.force:
        print(f"  ECA '{args.app_name}' already exists in the org")
        print("  Skipping deployment (use --force to redeploy)")
    else:
        if exists and args.force:
            print(f"  ECA exists but --force specified, redeploying...")
        else:
            print(f"  ECA '{args.app_name}' not found -- creating...")

        # Step 2: Generate metadata
        print("\n--- Step 2: Generate ECA metadata ---")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work_dir:
            generate_eca_metadata(
                work_dir, args.app_name, args.app_label, args.email,
            )
            generate_oauth_settings(work_dir, args.app_name, args.callback_url)

            # Step 3: Deploy
            print("\n--- Step 3: Deploy to Salesforce ---")
            if not deploy_eca(args.org, work_dir):
                sys.exit(1)

    # Summary
    print()
    print("=" * 60)
    print("  External Client App Setup Complete!")
    print("=" * 60)
    print()
    print("  MANUAL STEPS REQUIRED:")
    print()
    print("  1. Get Consumer Key + Secret from Salesforce Setup:")
    print(f"     Setup > App Manager > {args.app_label} > Manage Consumer Details")
    print("     Then run:")
    print("       azd env set SF_CONNECTED_APP_CLIENT_ID <consumer-key>")
    print("       azd env set SF_CONNECTED_APP_CLIENT_SECRET <consumer-secret>")
    print()
    print("  2. Enable PKCE (required for ApiHub OAuth):")
    print(f"     Setup > App Manager > {args.app_label} > Edit")
    print('     Check "Require Proof Key for Code Exchange (PKCE)"')
    print("     Save")
    print()
    print("  Next steps:")
    print(
        f"    3. Run: python scripts/configure-sf-connected-app.py"
        f" --app-name {args.app_name} --org {args.org}"
    )
    print("    4. Run: azd up")
    print("    5. Complete OAuth consent in browser when prompted")


if __name__ == "__main__":
    main()
