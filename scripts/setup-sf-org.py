"""Complete Salesforce org setup for OBO identity propagation.

Single entry point for all SF setup phases after creating a Dev Trial
and authenticating with `sf org login web`:

  Step 1/5: Connected App (JWT Bearer)      -- X.509 cert, OAuth scopes, profile pre-auth
  Step 2/5: SSO Federation (Entra OIDC)     -- Entra App + Auth Provider + Apex handler
  Step 3/5: Demo User + Test Data           -- Custom profile (no Account delete) + user + data
  Step 4/5: OBO Service Account             -- Dedicated user for JWT Bearer flow
  Step 5/5: Federation IDs                  -- Azure AD oid -> SF FederationIdentifier

Prerequisites:
- sf CLI authenticated to the target org: sf org login web --alias <alias>
- az CLI logged in (for SSO and Federation ID steps)

Usage:
    python scripts/setup-sf-org.py --org <alias> --email <email> --cert <pem>
    python scripts/setup-sf-org.py --org <alias> --email <email> --only demo svcacct
    python scripts/setup-sf-org.py --org <alias> --email <email> --skip sso --skip fedid
    python scripts/setup-sf-org.py --org <alias> --email <email> --only fedid --dry-run
    python scripts/setup-sf-org.py --org <alias> --email <email> --cleanup
"""

import argparse
import json
import os
import re
import sys
import tempfile
import textwrap
import time

from sf_utils import (
    run, run_interactive,
    get_org_info, get_org_domain, get_access_token,
    soql_query, tooling_query, query_profile_id, query_user,
    init_sfdx_project, deploy_metadata,
    sf_rest_post, create_setup_entity_access, assign_perm_set_to_user,
    write_temp_json, graph_patch,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Connected App (ECA step)
DEFAULT_APP_NAME = "Identity_PoC_OBO_JWT"
DEFAULT_APP_LABEL = "Identity PoC OBO JWT"
DEFAULT_PERM_SET_NAME = "MCP_OBO_Service_Account"
DEFAULT_PERM_SET_LABEL = "MCP OBO Service Account"

# Demo user
DEMO_PROFILE_NAME = "Standard User - No Delete"
DEMO_USER_ALIAS = "demondel"

# Service account
SVC_PROFILE_NAME = "Minimum Access - Salesforce"
SVC_USERNAME_PREFIX = "svc.mcp.obo"
SVC_ALIAS = "mcpobosv"

# Steps
STEPS = [
    ("eca",     "Connected App (JWT Bearer)"),
    ("sso",     "SSO Federation (Entra OIDC)"),
    ("demo",    "Demo User + Test Data"),
    ("svcacct", "OBO Service Account"),
    ("fedid",   "Federation IDs"),
]
STEP_KEYS = [s[0] for s in STEPS]


# ===================================================================
#  Step 1: Connected App (JWT Bearer) — was setup-sf-obo-eca.py
# ===================================================================

def _check_app_exists(org: str, app_name: str) -> bool:
    """Check if the ConnectedApp already exists via metadata retrieve."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        init_sfdx_project(d)
        result = run(
            f'sf project retrieve start --metadata "ConnectedApp:{app_name}" '
            f"-o {org}", cwd=d,
        )
        if result is None:
            return False
        meta_path = os.path.join(
            d, "force-app", "main", "default",
            "connectedApps", f"{app_name}.connectedApp-meta.xml",
        )
        return os.path.exists(meta_path)


def _generate_connected_app(work_dir: str, app_name: str, app_label: str,
                             email: str, cert_base64: str):
    """Generate ConnectedApp metadata XML with certificate for JWT Bearer."""
    init_sfdx_project(work_dir)
    print(f"  Generating ConnectedApp metadata for '{app_label}'...")

    app_xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<ConnectedApp xmlns="http://soap.sforce.com/2006/04/metadata">
    <contactEmail>{email}</contactEmail>
    <label>{app_label}</label>
    <oauthConfig>
        <callbackUrl>https://login.salesforce.com/services/oauth2/callback</callbackUrl>
        <certificate>{cert_base64}</certificate>
        <consumerKey></consumerKey>
        <isAdminApproved>true</isAdminApproved>
        <scopes>Api</scopes>
        <scopes>RefreshToken</scopes>
    </oauthConfig>
    <oauthPolicy>
        <ipRelaxation>ENFORCE</ipRelaxation>
        <refreshTokenPolicy>zero</refreshTokenPolicy>
    </oauthPolicy>
</ConnectedApp>
"""
    app_dir = os.path.join(
        work_dir, "force-app", "main", "default", "connectedApps",
    )
    os.makedirs(app_dir, exist_ok=True)
    path = os.path.join(app_dir, f"{app_name}.connectedApp-meta.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(app_xml)
    print(f"  Created: {os.path.relpath(path, work_dir)}")


def _generate_permission_set(work_dir: str, ps_name: str, ps_label: str):
    """Generate OBO service account Permission Set metadata."""
    init_sfdx_project(work_dir)
    print(f"  Generating Permission Set '{ps_label}'...")

    ps_xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>{ps_label}</label>
    <description>Minimal permissions for the OBO service account: API access and user lookup</description>
    <hasActivationRequired>false</hasActivationRequired>
    <userPermissions>
        <enabled>true</enabled>
        <name>ApiEnabled</name>
    </userPermissions>
    <userPermissions>
        <enabled>true</enabled>
        <name>ViewAllUsers</name>
    </userPermissions>
</PermissionSet>
"""
    ps_dir = os.path.join(
        work_dir, "force-app", "main", "default", "permissionsets",
    )
    os.makedirs(ps_dir, exist_ok=True)
    path = os.path.join(ps_dir, f"{ps_name}.permissionset-meta.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(ps_xml)
    print(f"  Created: {os.path.relpath(path, work_dir)}")


def _get_consumer_key(org: str, app_name: str) -> str | None:
    """Retrieve the auto-generated Consumer Key by re-retrieving metadata."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        init_sfdx_project(d)
        result = run(
            f'sf project retrieve start --metadata "ConnectedApp:{app_name}" '
            f"-o {org}", cwd=d,
        )
        if result is None:
            return None
        meta_path = os.path.join(
            d, "force-app", "main", "default",
            "connectedApps", f"{app_name}.connectedApp-meta.xml",
        )
        if not os.path.exists(meta_path):
            return None
        with open(meta_path, "r") as f:
            content = f.read()
        match = re.search(r"<consumerKey>(.*?)</consumerKey>", content)
        return match.group(1) if match else None


def _assign_profiles_to_app(org: str, app_name: str,
                             profile_names: list[str]) -> int:
    """Assign profiles to the ConnectedApp via SetupEntityAccess."""
    access_token, instance_url = get_access_token(org)
    if not access_token or not instance_url:
        print("  ERROR: Could not get org credentials")
        return 0

    # Get ConnectedApplication ID via Tooling API
    ca_records = tooling_query(
        org,
        f"SELECT Id FROM ConnectedApplication WHERE DeveloperName='{app_name}'",
    )
    if not ca_records:
        print("  ERROR: ConnectedApplication not found in Tooling API")
        return 0
    connected_app_id = ca_records[0]["Id"]

    assigned = 0
    for profile_name in profile_names:
        profile_id = query_profile_id(org, profile_name)
        if not profile_id:
            print(f"  WARNING: Profile '{profile_name}' not found")
            continue

        # Get PermissionSet owned by this Profile
        ps_records = soql_query(
            org,
            f"SELECT Id FROM PermissionSet WHERE ProfileId='{profile_id}'",
        )
        if not ps_records:
            print(f"  WARNING: PermissionSet for '{profile_name}' not found")
            continue

        ok = create_setup_entity_access(
            instance_url, access_token, ps_records[0]["Id"], connected_app_id,
        )
        if ok:
            print(f"  Assigned: {profile_name}")
            assigned += 1
        else:
            print(f"  FAILED: {profile_name}")

    return assigned


def step_eca(org: str, email: str, cert_path: str,
             app_name: str = DEFAULT_APP_NAME,
             app_label: str = DEFAULT_APP_LABEL,
             force: bool = False) -> dict:
    """Create Connected App with JWT Bearer flow + OBO Permission Set."""
    result = {"consumer_key": None, "app_name": app_name}

    # Read certificate
    cert_path = os.path.abspath(cert_path)
    if not os.path.exists(cert_path):
        print(f"  ERROR: Certificate file not found: {cert_path}")
        return result
    with open(cert_path, "r") as f:
        cert_pem = f.read().strip()
    cert_base64 = "".join(
        line for line in cert_pem.splitlines()
        if not line.startswith("-----")
    )
    print(f"  Certificate: {cert_path}")

    # Check existing app
    print("\n  --- Check existing ConnectedApp ---")
    exists = _check_app_exists(org, app_name)

    if exists and not force:
        print(f"  ConnectedApp '{app_name}' already exists (use --force to redeploy)")
    else:
        if exists:
            print(f"  App exists but --force specified, redeploying...")
        else:
            print(f"  ConnectedApp '{app_name}' not found -- creating...")

        print("\n  --- Generate metadata ---")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work_dir:
            _generate_connected_app(
                work_dir, app_name, app_label, email, cert_base64,
            )
            _generate_permission_set(
                work_dir, DEFAULT_PERM_SET_NAME, DEFAULT_PERM_SET_LABEL,
            )

            print("\n  --- Deploy to Salesforce ---")
            if not deploy_metadata(org, work_dir):
                return result

    # Get Consumer Key
    print("\n  --- Get Consumer Key ---")
    consumer_key = _get_consumer_key(org, app_name)
    if consumer_key:
        print(f"  Consumer Key: {consumer_key}")
        result["consumer_key"] = consumer_key
    else:
        print("  Could not retrieve Consumer Key via metadata retrieve")

    # Assign profiles for pre-authorization
    print("\n  --- Assign profiles for pre-authorization ---")
    profiles = ["System Administrator", "Standard User", DEMO_PROFILE_NAME]
    assigned = _assign_profiles_to_app(org, app_name, profiles)
    print(f"  {assigned} profile(s) assigned")

    return result


# ===================================================================
#  Step 2: SSO Federation (Entra OIDC) — was setup-salesforce-sso.py
# ===================================================================

def _sso_check_prerequisites():
    """Verify az CLI and sf CLI are available. Returns (tenant_id, has_sf_cli)."""
    account = run("az account show", parse_json=True)
    if not account:
        print("  ERROR: az CLI not logged in. Run 'az login' first.")
        return None, False
    tenant_id = account.get("tenantId", "")
    print(f"  az CLI: logged in (tenant: {tenant_id})")

    sf_version = run("sf --version")
    if not sf_version:
        print("\n  sf CLI not found.")
        print("  Install with: npm install -g @salesforce/cli")
        answer = input("  Continue without sf CLI? (y/N): ").strip().lower()
        if answer != "y":
            return None, False
        return tenant_id, False

    print(f"  sf CLI: {sf_version.splitlines()[0]}")
    return tenant_id, True


def _sso_create_entra_app(tenant_id: str):
    """Create Entra App Registration for Salesforce SSO (idempotent)."""
    env_name = os.environ.get("AZURE_ENV_NAME", "")
    display_name = f"Salesforce SSO ({env_name})" if env_name else "Salesforce SSO"

    app_id = run(
        f"az ad app list --filter \"displayName eq '{display_name}'\" "
        "--query \"[0].appId\" -o tsv"
    )
    if app_id:
        print(f"  Already exists: {app_id}")
    else:
        app_id = run(
            f'az ad app create --display-name "{display_name}" '
            "--sign-in-audience AzureADMyOrg --query appId -o tsv"
        )
        if not app_id:
            print("  ERROR: Failed to create app registration")
            return None, None, None
        print(f"  Created: {app_id}")

    obj_id = run(f'az ad app show --id "{app_id}" --query id -o tsv')

    # Set identifier URI
    uri = f"api://{app_id}"
    run(f'az ad app update --id "{app_id}" --identifier-uris "{uri}"')
    print(f"  Identifier URI: {uri}")

    # Configure optional ID token claims
    graph_patch(obj_id, {
        "optionalClaims": {
            "idToken": [
                {"name": "email", "essential": False},
                {"name": "given_name", "essential": False},
                {"name": "family_name", "essential": False},
                {"name": "preferred_username", "essential": False},
            ]
        }
    })
    print("  Optional claims: email, given_name, family_name, preferred_username")

    # Create client secret
    secret = run(
        f'az ad app credential reset --id "{app_id}" --query password -o tsv'
    )
    if not secret:
        print("  ERROR: Failed to create client secret")
        return None, None, None
    print(f"  Client secret: created (length: {len(secret)})")

    # Create service principal (idempotent)
    sp_id = run(f'az ad sp show --id "{app_id}" --query id -o tsv')
    if not sp_id:
        sp_id = run(f'az ad sp create --id "{app_id}" --query id -o tsv')
        print(f"  Service principal: created ({sp_id})")
    else:
        print(f"  Service principal: exists ({sp_id})")

    return app_id, obj_id, secret


def _sso_authenticate_salesforce(org: str, has_sf_cli: bool):
    """Authenticate to Salesforce org. Returns (instance_url, admin_username)."""
    if not has_sf_cli:
        print("  sf CLI not available -- manual input required")
        instance_url = input(
            "  Enter Salesforce instance URL "
            "(e.g., https://myorg.my.salesforce.com): "
        ).strip().rstrip("/")
        admin_username = input(
            "  Enter Salesforce admin username "
            "(e.g., admin@myorg.com): "
        ).strip()
        return instance_url, admin_username

    # Check if already authenticated
    org_info = run(f"sf org display -o {org} --json", parse_json=True)
    if org_info and org_info.get("status") == 0:
        result_data = org_info.get("result", {})
        instance_url = result_data.get("instanceUrl", "")
        admin_username = result_data.get("username", "")
        if instance_url:
            print(f"  Already authenticated: {instance_url}")
            print(f"  Admin user: {admin_username}")
            reuse = input("  Use this org? (Y/n): ").strip().lower()
            if reuse != "n":
                return instance_url, admin_username

    # Interactive browser login
    print("  Opening browser for Salesforce login...")
    rc = run_interactive(f"sf org login web --alias {org}")
    if rc != 0:
        print("  ERROR: Salesforce login failed")
        return None, None

    org_info = run(f"sf org display -o {org} --json", parse_json=True)
    if not org_info:
        print("  ERROR: Could not retrieve org info after login")
        return None, None

    result_data = org_info.get("result", {})
    instance_url = result_data.get("instanceUrl", "")
    admin_username = result_data.get("username", "")
    print(f"  Instance URL: {instance_url}")
    print(f"  Admin user: {admin_username}")
    return instance_url, admin_username


def _sso_update_redirect_uri(obj_id: str, instance_url: str):
    """Update Entra App with Salesforce callback URL + ID token issuance."""
    redirect_uri = f"{instance_url}/services/authcallback/AzureAD"
    graph_patch(obj_id, {
        "web": {
            "redirectUris": [redirect_uri],
            "implicitGrantSettings": {
                "enableIdTokenIssuance": True,
            },
        },
    })
    print(f"  Redirect URI: {redirect_uri}")
    print("  ID token issuance: enabled")


def _sso_generate_and_deploy(org: str, tenant_id: str, app_id: str,
                              secret: str, instance_url: str,
                              admin_username: str, has_sf_cli: bool) -> bool:
    """Generate Auth Provider metadata and deploy to Salesforce."""
    auth_provider_xml = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <AuthProvider xmlns="http://soap.sforce.com/2006/04/metadata">
            <friendlyName>Azure AD</friendlyName>
            <providerType>OpenIdConnect</providerType>
            <authorizeUrl>https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize</authorizeUrl>
            <tokenUrl>https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token</tokenUrl>
            <userInfoUrl>https://graph.microsoft.com/oidc/userinfo</userInfoUrl>
            <defaultScopes>openid email profile offline_access</defaultScopes>
            <consumerKey>{app_id}</consumerKey>
            <consumerSecret>{secret}</consumerSecret>
            <registrationHandler>AzureADRegistrationHandler</registrationHandler>
            <executionUser>{admin_username}</executionUser>
            <sendAccessTokenInHeader>true</sendAccessTokenInHeader>
            <sendClientCredentialsInHeader>false</sendClientCredentialsInHeader>
        </AuthProvider>
    """)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work_dir:
        init_sfdx_project(work_dir)

        # Write Auth Provider metadata
        ap_dir = os.path.join(
            work_dir, "force-app", "main", "default", "authproviders",
        )
        os.makedirs(ap_dir, exist_ok=True)
        ap_path = os.path.join(ap_dir, "AzureAD.authprovider-meta.xml")
        with open(ap_path, "w", encoding="utf-8") as f:
            f.write(auth_provider_xml)
        print(f"  Generated: authproviders/AzureAD.authprovider-meta.xml")

        # Check for Apex Registration Handler in repo
        repo_cls_dir = os.path.join(
            REPO_ROOT, "salesforce", "force-app", "main", "default", "classes",
        )
        cls_name = "AzureADRegistrationHandler"
        repo_cls = os.path.join(repo_cls_dir, f"{cls_name}.cls")
        repo_meta = os.path.join(repo_cls_dir, f"{cls_name}.cls-meta.xml")

        if os.path.exists(repo_cls) and os.path.exists(repo_meta):
            # Copy Apex class into temp deploy dir
            cls_dir = os.path.join(
                work_dir, "force-app", "main", "default", "classes",
            )
            os.makedirs(cls_dir, exist_ok=True)
            import shutil
            shutil.copy2(repo_cls, cls_dir)
            shutil.copy2(repo_meta, cls_dir)
            print(f"  Included: classes/{cls_name}.cls")
        else:
            print(f"  WARNING: {cls_name}.cls not found in repo")
            print("  The Auth Provider requires this Apex class to be deployed first")

        # Deploy
        if not has_sf_cli:
            print("  SKIPPED: sf CLI not available -- deploy manually")
            return False

        print("  Deploying Auth Provider to Salesforce...")
        return deploy_metadata(org, work_dir)


def step_sso(org: str) -> dict:
    """Configure SSO Federation between Azure AD and Salesforce."""
    result = {"app_id": None, "instance_url": None}

    # Prerequisites
    print("\n  --- Prerequisites ---")
    tenant_id, has_sf_cli = _sso_check_prerequisites()
    if tenant_id is None:
        return result

    # Create Entra App
    print("\n  --- Create Entra App Registration ---")
    app_id, obj_id, secret = _sso_create_entra_app(tenant_id)
    if not app_id:
        return result
    result["app_id"] = app_id

    # Authenticate to Salesforce
    print("\n  --- Authenticate to Salesforce ---")
    instance_url, admin_username = _sso_authenticate_salesforce(org, has_sf_cli)
    if not instance_url:
        return result
    result["instance_url"] = instance_url

    # Update redirect URI
    print("\n  --- Update Redirect URI ---")
    _sso_update_redirect_uri(obj_id, instance_url)

    # Generate metadata and deploy
    print("\n  --- Generate and Deploy Auth Provider ---")
    _sso_generate_and_deploy(
        org, tenant_id, app_id, secret,
        instance_url, admin_username, has_sf_cli,
    )

    # Verify
    print("\n  --- Verify ---")
    app = run(
        f'az ad app show --id "{app_id}" '
        '--query "{{appId:appId, redirectUris:web.redirectUris}}" -o json',
        parse_json=True,
    )
    if app:
        print(f"  Entra App ID:     {app.get('appId', '?')}")
        print(f"  Redirect URIs:    {app.get('redirectUris', [])}")

    sso_url = f"{instance_url}/services/auth/sso/AzureAD"
    print(f"\n  Test SSO: {sso_url}")
    print("  Next: Enable 'Azure AD' on My Domain login page")
    print("    Setup > My Domain > Authentication Configuration > Edit")

    return result


# ===================================================================
#  Step 3: Demo User + Test Data — was setup-sf-demo-user.py
# ===================================================================

DEMO_PROFILE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<Profile xmlns="http://soap.sforce.com/2006/04/metadata">
    <custom>true</custom>
    <userLicense>Salesforce</userLicense>
    <userPermissions>
        <enabled>true</enabled>
        <name>ApiEnabled</name>
    </userPermissions>
    <userPermissions>
        <enabled>true</enabled>
        <name>LightningExperienceUser</name>
    </userPermissions>
    <userPermissions>
        <enabled>true</enabled>
        <name>RunReports</name>
    </userPermissions>
    <userPermissions>
        <enabled>true</enabled>
        <name>ExportReport</name>
    </userPermissions>
    <objectPermissions>
        <allowCreate>true</allowCreate>
        <allowDelete>false</allowDelete>
        <allowEdit>true</allowEdit>
        <allowRead>true</allowRead>
        <modifyAllRecords>false</modifyAllRecords>
        <object>Account</object>
        <viewAllRecords>false</viewAllRecords>
    </objectPermissions>
    <objectPermissions>
        <allowCreate>true</allowCreate>
        <allowDelete>true</allowDelete>
        <allowEdit>true</allowEdit>
        <allowRead>true</allowRead>
        <modifyAllRecords>false</modifyAllRecords>
        <object>Contact</object>
        <viewAllRecords>false</viewAllRecords>
    </objectPermissions>
    <objectPermissions>
        <allowCreate>true</allowCreate>
        <allowDelete>true</allowDelete>
        <allowEdit>true</allowEdit>
        <allowRead>true</allowRead>
        <modifyAllRecords>false</modifyAllRecords>
        <object>Opportunity</object>
        <viewAllRecords>false</viewAllRecords>
    </objectPermissions>
    <objectPermissions>
        <allowCreate>true</allowCreate>
        <allowDelete>true</allowDelete>
        <allowEdit>true</allowEdit>
        <allowRead>true</allowRead>
        <modifyAllRecords>false</modifyAllRecords>
        <object>Case</object>
        <viewAllRecords>false</viewAllRecords>
    </objectPermissions>
    <objectPermissions>
        <allowCreate>true</allowCreate>
        <allowDelete>true</allowDelete>
        <allowEdit>true</allowEdit>
        <allowRead>true</allowRead>
        <modifyAllRecords>false</modifyAllRecords>
        <object>Lead</object>
        <viewAllRecords>false</viewAllRecords>
    </objectPermissions>
    <objectPermissions>
        <allowCreate>true</allowCreate>
        <allowDelete>true</allowDelete>
        <allowEdit>true</allowEdit>
        <allowRead>true</allowRead>
        <modifyAllRecords>false</modifyAllRecords>
        <object>Task</object>
        <viewAllRecords>false</viewAllRecords>
    </objectPermissions>
    <objectPermissions>
        <allowCreate>true</allowCreate>
        <allowDelete>true</allowDelete>
        <allowEdit>true</allowEdit>
        <allowRead>true</allowRead>
        <modifyAllRecords>false</modifyAllRecords>
        <object>Event</object>
        <viewAllRecords>false</viewAllRecords>
    </objectPermissions>
    <tabVisibilities>
        <tab>standard-Account</tab>
        <visibility>DefaultOn</visibility>
    </tabVisibilities>
    <tabVisibilities>
        <tab>standard-Contact</tab>
        <visibility>DefaultOn</visibility>
    </tabVisibilities>
    <tabVisibilities>
        <tab>standard-Opportunity</tab>
        <visibility>DefaultOn</visibility>
    </tabVisibilities>
    <tabVisibilities>
        <tab>standard-report</tab>
        <visibility>DefaultOn</visibility>
    </tabVisibilities>
    <tabVisibilities>
        <tab>standard-Dashboard</tab>
        <visibility>DefaultOn</visibility>
    </tabVisibilities>
</Profile>
"""


def _create_demo_profile(org: str) -> str | None:
    """Deploy the 'Standard User - No Delete' profile. Returns profile ID."""
    profile_id = query_profile_id(org, DEMO_PROFILE_NAME)
    if profile_id:
        print(f"  Profile '{DEMO_PROFILE_NAME}' already exists: {profile_id}")
        return profile_id

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work_dir:
        init_sfdx_project(work_dir)
        profile_dir = os.path.join(
            work_dir, "force-app", "main", "default", "profiles",
        )
        os.makedirs(profile_dir, exist_ok=True)
        path = os.path.join(profile_dir, f"{DEMO_PROFILE_NAME}.profile-meta.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEMO_PROFILE_XML)
        print(f"  Generated '{DEMO_PROFILE_NAME}' profile metadata")

        if not deploy_metadata(org, work_dir):
            return None

    profile_id = query_profile_id(org, DEMO_PROFILE_NAME)
    if not profile_id:
        print("  ERROR: Profile deployed but ID not found")
    else:
        print(f"  Profile ID: {profile_id}")
    return profile_id


def _create_demo_user(org: str, profile_id: str,
                       username: str, email: str) -> str | None:
    """Create the demo user. Returns user ID."""
    existing = query_user(org, username)
    if existing:
        user_id = existing["Id"]
        print(f"  User '{username}' already exists: {user_id}")
        if existing.get("ProfileId") != profile_id:
            print(f"  Updating profile to '{DEMO_PROFILE_NAME}'...")
            run(
                f'sf data update record --sobject User --record-id {user_id} '
                f'--values "ProfileId=\'{profile_id}\'" -o {org} --json',
            )
        return user_id

    print(f"  Creating demo user: {username}")
    values = (
        f"Username='{username}' "
        f"Email='{email}' "
        f"LastName='User' "
        f"FirstName='Demo' "
        f"Alias='{DEMO_USER_ALIAS}' "
        f"ProfileId='{profile_id}' "
        f"TimeZoneSidKey='Europe/London' "
        f"LocaleSidKey='en_US' "
        f"EmailEncodingKey='UTF-8' "
        f"LanguageLocaleKey='en_US' "
        f"IsActive=true"
    )
    result = run(
        f'sf data create record --sobject User --values "{values}" '
        f"-o {org} --json",
        parse_json=True,
    )
    if not result or not result.get("result", {}).get("id"):
        print("  ERROR: Failed to create demo user")
        return None
    user_id = result["result"]["id"]
    print(f"  Created user: {user_id}")
    return user_id


def _reset_user_password(org: str, user_id: str):
    """Reset user password. SF sends a password reset email."""
    print("  Resetting password (SF will send reset email)...")
    result = run(
        f"sf org generate password --sobject User "
        f"--on-behalf-of {user_id} -o {org} --json",
        parse_json=True,
    )
    if result and result.get("result"):
        password = result["result"].get("password", "(check email)")
        print(f"  Password set: {password}")
    else:
        print("  NOTE: Password generation may not be supported. User will receive a reset email.")


def _create_test_data(org: str) -> list[dict]:
    """Create sample Accounts and Contacts for the demo."""
    print("  Creating test data...")
    created = []

    accounts = [
        {"Name": "Acme Corporation", "Industry": "Technology"},
        {"Name": "Global Industries", "Industry": "Manufacturing"},
        {"Name": "Pinnacle Consulting", "Industry": "Consulting"},
    ]

    for acct in accounts:
        existing = soql_query(org, f"SELECT Id FROM Account WHERE Name = '{acct['Name']}'")
        if existing:
            acct_id = existing[0]["Id"]
            print(f"  Account '{acct['Name']}' already exists: {acct_id}")
        else:
            values = " ".join(f"{k}='{v}'" for k, v in acct.items())
            result = run(
                f'sf data create record --sobject Account --values "{values}" '
                f"-o {org} --json",
                parse_json=True,
            )
            if result and result.get("result", {}).get("id"):
                acct_id = result["result"]["id"]
                print(f"  Created Account '{acct['Name']}': {acct_id}")
            else:
                print(f"  ERROR: Failed to create Account '{acct['Name']}'")
                continue
        created.append({"type": "Account", "name": acct["Name"], "id": acct_id})

    # Contacts linked to first account
    if created:
        acme_id = created[0]["id"]
        contacts = [
            {"FirstName": "Jane", "LastName": "Smith",
             "Email": "jane.smith@acmecorp.example.com"},
            {"FirstName": "Bob", "LastName": "Johnson",
             "Email": "bob.johnson@acmecorp.example.com"},
        ]
        for contact in contacts:
            full_name = f"{contact['FirstName']} {contact['LastName']}"
            existing = soql_query(
                org,
                f"SELECT Id FROM Contact WHERE FirstName = '{contact['FirstName']}' "
                f"AND LastName = '{contact['LastName']}' AND AccountId = '{acme_id}'",
            )
            if existing:
                print(f"  Contact '{full_name}' already exists: {existing[0]['Id']}")
                created.append({"type": "Contact", "name": full_name, "id": existing[0]["Id"]})
            else:
                values = " ".join(f"{k}='{v}'" for k, v in contact.items())
                values += f" AccountId='{acme_id}'"
                result = run(
                    f'sf data create record --sobject Contact --values "{values}" '
                    f"-o {org} --json",
                    parse_json=True,
                )
                if result and result.get("result", {}).get("id"):
                    cid = result["result"]["id"]
                    print(f"  Created Contact '{full_name}': {cid}")
                    created.append({"type": "Contact", "name": full_name, "id": cid})
                else:
                    print(f"  ERROR: Failed to create Contact '{full_name}'")

    return created


def step_demo(org: str, email: str, username: str | None = None) -> dict:
    """Create demo user with restricted profile and test data."""
    domain = get_org_domain(org)
    if not domain:
        print("  ERROR: Could not determine org domain")
        return {"username": None}

    username = username or f"demo.nodelete@{domain}"
    print(f"  Demo user:   {username}")
    print(f"  Profile:     {DEMO_PROFILE_NAME}")

    # Create custom profile
    print("\n  --- Create custom profile ---")
    profile_id = _create_demo_profile(org)
    if not profile_id:
        return {"username": username}

    # Create demo user
    print("\n  --- Create demo user ---")
    user_id = _create_demo_user(org, profile_id, username, email)
    if not user_id:
        return {"username": username}

    # Reset password
    print("\n  --- Reset password ---")
    _reset_user_password(org, user_id)

    # Create test data
    print("\n  --- Create test data ---")
    test_data = _create_test_data(org)

    print(f"\n  Demo user ready: {username}")
    print(f"  Test data: {len(test_data)} records")
    return {"username": username, "user_id": user_id, "test_data": test_data}


# ===================================================================
#  Step 4: OBO Service Account — was setup-sf-service-account.py
# ===================================================================

def _create_service_account(org: str, profile_id: str,
                             username: str, email: str) -> str | None:
    """Create the service account user. Returns user ID."""
    existing = query_user(org, username)
    if existing:
        user_id = existing["Id"]
        is_active = existing.get("IsActive", False)
        print(f"  User '{username}' already exists: {user_id}")
        if not is_active:
            print("  User is inactive -- reactivating...")
            run(
                f'sf data update record --sobject User --record-id {user_id} '
                f'--values "IsActive=true" -o {org} --json',
            )
            print("  User reactivated")
        if existing.get("ProfileId") != profile_id:
            print(f"  Updating profile to '{SVC_PROFILE_NAME}'...")
            run(
                f'sf data update record --sobject User --record-id {user_id} '
                f'--values "ProfileId=\'{profile_id}\'" -o {org} --json',
            )
        return user_id

    print(f"  Creating service account: {username}")
    values = (
        f"Username='{username}' "
        f"Email='{email}' "
        f"LastName='Service Account' "
        f"FirstName='MCP OBO' "
        f"Alias='{SVC_ALIAS}' "
        f"ProfileId='{profile_id}' "
        f"TimeZoneSidKey='Europe/London' "
        f"LocaleSidKey='en_US' "
        f"EmailEncodingKey='UTF-8' "
        f"LanguageLocaleKey='en_US' "
        f"IsActive=true"
    )
    result = run(
        f'sf data create record --sobject User --values "{values}" '
        f"-o {org} --json",
        parse_json=True,
    )
    if not result or not result.get("result", {}).get("id"):
        print("  ERROR: Failed to create service account")
        return None
    user_id = result["result"]["id"]
    print(f"  Created user: {user_id}")
    return user_id


def _assign_svc_permission_set(org: str, user_id: str, username: str) -> bool:
    """Assign MCP_OBO_Service_Account Permission Set to the user."""
    ps_records = soql_query(
        org,
        f"SELECT Id FROM PermissionSet "
        f"WHERE Name = '{DEFAULT_PERM_SET_NAME}' AND IsOwnedByProfile = false",
    )
    if not ps_records:
        print(f"  WARNING: Permission Set '{DEFAULT_PERM_SET_NAME}' not found")
        print("  Run the 'eca' step first to deploy it")
        return False
    ps_id = ps_records[0]["Id"]
    print(f"  Found Permission Set: {DEFAULT_PERM_SET_NAME} ({ps_id})")

    access_token, instance_url = get_access_token(org)
    if not access_token or not instance_url:
        print("  ERROR: Could not get org credentials")
        return False

    ok = assign_perm_set_to_user(instance_url, access_token, ps_id, user_id)
    if ok:
        print(f"  Assigned Permission Set to {username}")
    else:
        print(f"  FAILED to assign Permission Set to {username}")
    return ok


def _preauthorize_svc_for_app(org: str, app_name: str) -> bool:
    """Pre-authorize the OBO Permission Set for the Connected App."""
    access_token, instance_url = get_access_token(org)
    if not access_token or not instance_url:
        print("  ERROR: Could not get org credentials")
        return False

    # Get ConnectedApplication ID
    ca_records = tooling_query(
        org,
        f"SELECT Id FROM ConnectedApplication WHERE DeveloperName='{app_name}'",
    )
    if not ca_records:
        print(f"  WARNING: Connected App '{app_name}' not found")
        print("  Run the 'eca' step first to deploy it")
        return False
    connected_app_id = ca_records[0]["Id"]

    # Get Permission Set ID
    ps_records = soql_query(
        org,
        f"SELECT Id FROM PermissionSet "
        f"WHERE Name = '{DEFAULT_PERM_SET_NAME}' AND IsOwnedByProfile = false",
    )
    if not ps_records:
        print(f"  WARNING: Permission Set '{DEFAULT_PERM_SET_NAME}' not found")
        return False
    ps_id = ps_records[0]["Id"]

    ok = create_setup_entity_access(
        instance_url, access_token, ps_id, connected_app_id,
    )
    if ok:
        print(f"  Pre-authorized Permission Set for Connected App '{app_name}'")
    else:
        print(f"  FAILED to pre-authorize")
    return ok


def step_svcacct(org: str, email: str,
                  app_name: str = DEFAULT_APP_NAME,
                  username: str | None = None) -> dict:
    """Create dedicated service account for OBO JWT Bearer flow."""
    domain = get_org_domain(org)
    if not domain:
        print("  ERROR: Could not determine org domain")
        return {"username": None}

    username = username or f"{SVC_USERNAME_PREFIX}@{domain}"
    print(f"  Service account:   {username}")
    print(f"  Profile:           {SVC_PROFILE_NAME}")
    print(f"  Permission Set:    {DEFAULT_PERM_SET_NAME}")

    # Resolve profile
    print("\n  --- Resolve profile ---")
    profile_id = query_profile_id(org, SVC_PROFILE_NAME)
    if not profile_id:
        print(f"  ERROR: Profile '{SVC_PROFILE_NAME}' not found")
        return {"username": username}
    print(f"  Profile '{SVC_PROFILE_NAME}': {profile_id}")

    # Create service account
    print("\n  --- Create service account ---")
    user_id = _create_service_account(org, profile_id, username, email)
    if not user_id:
        return {"username": username}

    # Assign Permission Set
    print("\n  --- Assign Permission Set ---")
    _assign_svc_permission_set(org, user_id, username)

    # Pre-authorize for Connected App
    print("\n  --- Pre-authorize for Connected App ---")
    _preauthorize_svc_for_app(org, app_name)

    print(f"\n  Service account ready: {username}")
    return {"username": username, "user_id": user_id}


# ===================================================================
#  Step 5: Federation IDs — was set-sf-federation-id.py
# ===================================================================

def _lookup_azure_ad_oid(email: str) -> str | None:
    """Look up the Azure AD object ID for a user by email/UPN."""
    return run(f'az ad user show --id "{email}" --query id -o tsv')


def step_fedid(org: str, dry_run: bool = False,
                users: list[str] | None = None) -> dict:
    """Set FederationIdentifier on SF users from their Azure AD oid."""
    if dry_run:
        print("\n  ** DRY RUN -- no changes will be made **")

    # Query SF users
    print("\n  --- Query Salesforce users ---")
    soql = (
        "SELECT Id, Username, Email, FederationIdentifier "
        "FROM User "
        "WHERE IsActive = true AND UserType = 'Standard'"
    )
    if users:
        email_list = "', '".join(users)
        soql += f" AND (Email IN ('{email_list}') OR Username IN ('{email_list}'))"

    sf_users = soql_query(org, soql)
    if not sf_users:
        print("  No matching users found")
        return {"updated": 0, "skipped": 0, "not_found": 0, "failed": 0}
    print(f"  Found {len(sf_users)} user(s)")

    # Match to Azure AD and update
    print("\n  --- Match Azure AD and update FederationIdentifier ---")
    updated = 0
    skipped = 0
    failed = 0
    not_found = 0

    for user in sf_users:
        user_id = user.get("Id", "")
        username = user.get("Username", "")
        email = user.get("Email", "")
        current_fed_id = user.get("FederationIdentifier") or ""

        lookup_id = email or username
        if not lookup_id:
            print(f"  SKIP: User {user_id} has no email or username")
            skipped += 1
            continue

        print(f"\n  User: {lookup_id}")
        print(f"    SF Id:          {user_id}")
        print(f"    Current FedId:  {current_fed_id or '(empty)'}")

        # Look up Azure AD oid
        oid = _lookup_azure_ad_oid(lookup_id)
        if oid is None and username and username != email:
            print(f"    Email not found in Azure AD, trying username: {username}")
            oid = _lookup_azure_ad_oid(username)

        if oid is None:
            print(f"    NOT FOUND in Azure AD -- skipping")
            not_found += 1
            continue

        print(f"    Azure AD oid:   {oid}")

        if current_fed_id == oid:
            print(f"    Already set correctly -- skipping")
            skipped += 1
            continue

        if dry_run:
            print(f"    WOULD SET FederationIdentifier = {oid}")
            updated += 1
        else:
            result = run(
                f'sf data update record -o {org} -s User -i {user_id} '
                f'-v "FederationIdentifier={oid}"',
            )
            if result is not None:
                print(f"    UPDATED FederationIdentifier = {oid}")
                updated += 1
            else:
                print(f"    FAILED to update")
                failed += 1

    action = "Would update" if dry_run else "Updated"
    print(f"\n  {action}: {updated}  |  Skipped: {skipped}  |  "
          f"Not found: {not_found}  |  Failed: {failed}")

    return {"updated": updated, "skipped": skipped,
            "not_found": not_found, "failed": failed}


# ===================================================================
#  Cleanup
# ===================================================================

def cleanup_all(org: str, email: str):
    """Deactivate demo user + service account, delete test data."""
    domain = get_org_domain(org)
    if not domain:
        print("  ERROR: Could not determine org domain")
        return

    # Deactivate demo user
    demo_username = f"demo.nodelete@{domain}"
    print(f"\n  --- Demo user: {demo_username} ---")
    demo_user = query_user(org, demo_username)
    if demo_user:
        user_id = demo_user["Id"]
        if not demo_user.get("IsActive", True):
            print(f"  Already inactive")
        else:
            print(f"  Deactivating ({user_id})...")
            run(
                f'sf data update record --sobject User --record-id {user_id} '
                f'--values "IsActive=false" -o {org} --json',
            )
            print("  Deactivated")
    else:
        print(f"  Not found -- skipping")

    # Delete test data
    print("\n  --- Delete test data ---")
    for name in ["Pinnacle Consulting", "Global Industries", "Acme Corporation"]:
        records = soql_query(org, f"SELECT Id FROM Account WHERE Name = '{name}'")
        for rec in records:
            print(f"  Deleting Account '{name}' ({rec['Id']})...")
            run(f'sf data delete record --sobject Account --record-id {rec["Id"]} -o {org}')
    print(f"  NOTE: Profile '{DEMO_PROFILE_NAME}' not deleted (remove manually if needed)")

    # Deactivate service account
    svc_username = f"{SVC_USERNAME_PREFIX}@{domain}"
    print(f"\n  --- Service account: {svc_username} ---")
    svc_user = query_user(org, svc_username)
    if svc_user:
        user_id = svc_user["Id"]
        if not svc_user.get("IsActive", True):
            print(f"  Already inactive")
        else:
            print(f"  Deactivating ({user_id})...")
            run(
                f'sf data update record --sobject User --record-id {user_id} '
                f'--values "IsActive=false" -o {org} --json',
            )
            print("  Deactivated")
    else:
        print(f"  Not found -- skipping")

    print("\n  Cleanup complete")


# ===================================================================
#  Orchestrator
# ===================================================================

def check_prerequisites(org: str):
    """Verify sf CLI is authenticated to the target org."""
    print("--- Prerequisites ---")
    info = get_org_info(org)
    if not info:
        print(f"\n  ERROR: sf CLI not authenticated to org '{org}'")
        print(f"  Run: sf org login web --alias {org}")
        sys.exit(1)

    instance_url = info.get("instanceUrl", "")
    username = info.get("username", "")
    print(f"  SF org:      {instance_url}")
    print(f"  Admin user:  {username}")


def _print_summary(results: dict, steps_to_run: set, start_time: float,
                    step_results: dict):
    """Print final setup summary."""
    elapsed = time.time() - start_time

    print()
    print("#" * 60)
    print("#  Setup Summary")
    print("#" * 60)
    print()

    for key, label in STEPS:
        if key in steps_to_run:
            status = results.get(key, "NOT RUN")
            if status == "OK":
                marker = " [OK]  "
            elif status == "FAILED":
                marker = " [FAIL]"
            else:
                marker = " [SKIP]"
        else:
            marker = " [SKIP]"
        print(f"  {marker} {label}")

    print()
    print(f"  Elapsed: {elapsed:.0f}s")

    # Show key outputs from steps
    eca_result = step_results.get("eca", {})
    svcacct_result = step_results.get("svcacct", {})
    demo_result = step_results.get("demo", {})

    consumer_key = eca_result.get("consumer_key")
    svc_username = svcacct_result.get("username")
    demo_username = demo_result.get("username")

    print()
    print("  MANUAL STEPS REMAINING:")
    if consumer_key:
        print(f"  1. Set Connected App consumer key in azd:")
        print(f'     azd env set SF_CONNECTED_APP_CLIENT_ID "{consumer_key}"')
    else:
        print("  1. Get Consumer Key from SF Setup > App Manager, then:")
        print("     azd env set SF_CONNECTED_APP_CLIENT_ID <consumer-key>")

    print("  2. Upload PFX certificate to Azure Key Vault as 'sf-jwt-bearer'")
    print("  3. Set cert thumbprint:")
    print("     azd env set SF_JWT_BEARER_CERT_THUMBPRINT <thumbprint>")

    if svc_username:
        print(f"  4. Set service account:")
        print(f'     azd env set SF_SERVICE_ACCOUNT_USERNAME "{svc_username}"')
    else:
        print("  4. azd env set SF_SERVICE_ACCOUNT_USERNAME <svc@your-org.my.salesforce.com>")

    print("  5. Enable 'Azure AD' on My Domain login page (if SSO was configured):")
    print("     Setup > My Domain > Authentication Configuration > Edit")
    print("  6. Deploy: azd up")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Complete Salesforce org setup for OBO identity propagation. "
        "Chains all setup phases: ECA -> SSO -> Demo User -> Service Account -> Federation IDs."
    )
    parser.add_argument(
        "--org", required=True,
        help="Salesforce org alias (as authenticated with 'sf org login web')",
    )
    parser.add_argument(
        "--email", required=True,
        help="Admin email (used for ECA contact + demo user password reset)",
    )
    parser.add_argument(
        "--cert", default=None,
        help="Path to X.509 certificate PEM file (required when 'eca' step runs)",
    )
    parser.add_argument(
        "--skip", nargs="+", choices=STEP_KEYS, default=[],
        help="Steps to skip (e.g., --skip sso fedid)",
    )
    parser.add_argument(
        "--only", nargs="+", choices=STEP_KEYS, default=[],
        help="Run only these steps (e.g., --only eca demo)",
    )
    parser.add_argument(
        "--continue-on-error", action="store_true",
        help="Continue with remaining steps if a step fails",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Redeploy Connected App even if it exists",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="For 'fedid' step: show changes without applying",
    )
    parser.add_argument(
        "--user", action="append", dest="users", metavar="EMAIL",
        help="For 'fedid' step: target specific user(s) by email (repeatable)",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Deactivate demo user + service account, delete test data",
    )
    args = parser.parse_args()

    print()
    print("#" * 60)
    print("#  Salesforce Org Setup -- Consolidated")
    print("#" * 60)
    print()
    print(f"  Org alias:  {args.org}")
    print(f"  Email:      {args.email}")
    print()

    # Determine which steps to run (before prerequisites, for fast --cert validation)
    if not args.cleanup:
        if args.only:
            steps_to_run = set(args.only)
        else:
            steps_to_run = set(STEP_KEYS) - set(args.skip)

        # Validate --cert before network calls
        if "eca" in steps_to_run and not args.cert:
            print("  ERROR: --cert is required when the 'eca' step runs")
            print("  Either provide --cert <pem> or skip with --skip eca")
            sys.exit(1)

    check_prerequisites(args.org)

    # Cleanup mode
    if args.cleanup:
        cleanup_all(args.org, args.email)
        return

    # Show step plan
    print()
    print("  Steps:")
    for key, label in STEPS:
        status = "RUN " if key in steps_to_run else "SKIP"
        print(f"    [{status}] {label}")
    print()

    total = sum(1 for k in STEP_KEYS if k in steps_to_run)
    step_num = 0
    results = {}
    step_results = {}
    start_time = time.time()

    # Step 1: Connected App (ECA)
    if "eca" in steps_to_run:
        step_num += 1
        print()
        print("=" * 60)
        print(f"  Step {step_num}/{total}: Connected App (JWT Bearer)")
        print("=" * 60)
        print()
        try:
            sr = step_eca(
                args.org, args.email, args.cert,
                force=args.force,
            )
            step_results["eca"] = sr
            results["eca"] = "OK"
        except Exception as e:
            print(f"\n  ERROR: {e}")
            results["eca"] = "FAILED"
            step_results["eca"] = {}
        if results.get("eca") == "FAILED" and not args.continue_on_error:
            print("\n  Stopping (use --continue-on-error to proceed past failures)")
            _print_summary(results, steps_to_run, start_time, step_results)
            sys.exit(1)

    # Step 2: SSO Federation
    if "sso" in steps_to_run:
        step_num += 1
        print()
        print("=" * 60)
        print(f"  Step {step_num}/{total}: SSO Federation (Entra OIDC)")
        print("=" * 60)
        print()
        try:
            sr = step_sso(args.org)
            step_results["sso"] = sr
            results["sso"] = "OK"
        except Exception as e:
            print(f"\n  ERROR: {e}")
            results["sso"] = "FAILED"
            step_results["sso"] = {}
        if results.get("sso") == "FAILED" and not args.continue_on_error:
            print("\n  Stopping (use --continue-on-error to proceed past failures)")
            _print_summary(results, steps_to_run, start_time, step_results)
            sys.exit(1)

    # Step 3: Demo User + Test Data
    if "demo" in steps_to_run:
        step_num += 1
        print()
        print("=" * 60)
        print(f"  Step {step_num}/{total}: Demo User + Test Data")
        print("=" * 60)
        print()
        try:
            sr = step_demo(args.org, args.email)
            step_results["demo"] = sr
            results["demo"] = "OK"
        except Exception as e:
            print(f"\n  ERROR: {e}")
            results["demo"] = "FAILED"
            step_results["demo"] = {}
        if results.get("demo") == "FAILED" and not args.continue_on_error:
            print("\n  Stopping (use --continue-on-error to proceed past failures)")
            _print_summary(results, steps_to_run, start_time, step_results)
            sys.exit(1)

    # Step 4: OBO Service Account
    if "svcacct" in steps_to_run:
        step_num += 1
        print()
        print("=" * 60)
        print(f"  Step {step_num}/{total}: OBO Service Account")
        print("=" * 60)
        print()
        try:
            sr = step_svcacct(args.org, args.email)
            step_results["svcacct"] = sr
            results["svcacct"] = "OK"
        except Exception as e:
            print(f"\n  ERROR: {e}")
            results["svcacct"] = "FAILED"
            step_results["svcacct"] = {}
        if results.get("svcacct") == "FAILED" and not args.continue_on_error:
            print("\n  Stopping (use --continue-on-error to proceed past failures)")
            _print_summary(results, steps_to_run, start_time, step_results)
            sys.exit(1)

    # Step 5: Federation IDs
    if "fedid" in steps_to_run:
        step_num += 1
        print()
        print("=" * 60)
        print(f"  Step {step_num}/{total}: Federation IDs")
        print("=" * 60)
        print()
        try:
            sr = step_fedid(args.org, dry_run=args.dry_run, users=args.users)
            step_results["fedid"] = sr
            failed = sr.get("failed", 0)
            results["fedid"] = "FAILED" if failed > 0 else "OK"
        except Exception as e:
            print(f"\n  ERROR: {e}")
            results["fedid"] = "FAILED"
            step_results["fedid"] = {}

    _print_summary(results, steps_to_run, start_time, step_results)

    if any(v == "FAILED" for v in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
