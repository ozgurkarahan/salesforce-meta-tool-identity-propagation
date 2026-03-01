"""Create a dedicated Salesforce service account for OBO (JWT Bearer) flow.

The OBO flow uses a service account to obtain a Salesforce token for SOQL
user lookups (FederationIdentifier → Username mapping). This script creates
a dedicated user with the most restrictive base profile and assigns the
MCP_OBO_Service_Account Permission Set for minimal required permissions.

Creates:
1. A user with "Minimum Access - Salesforce" profile
2. Assigns Permission Set "MCP_OBO_Service_Account" (if deployed)
3. Pre-authorizes the Permission Set for the Connected App (if deployed)

The service account does NOT need:
- System Administrator profile (Permission Set provides all needed access)
- FederationIdentifier (never identity-mapped, only used for SOQL lookups)
- A password (JWT Bearer flow uses certificate auth)

Prerequisites:
- Salesforce CLI (`sf`) installed and authenticated to the target org
  Run: sf org login web --alias <alias>
- Permission Set MCP_OBO_Service_Account deployed (by setup-sf-obo-eca.py)
- Connected App Identity_PoC_OBO_JWT deployed (by setup-sf-obo-eca.py)

Usage:
    python scripts/setup-sf-service-account.py --org <alias> --email <your-email>
    python scripts/setup-sf-service-account.py --org <alias> --email <your-email> --cleanup
"""

import argparse
import json
import os
import subprocess
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DEFAULT_USERNAME_PREFIX = "svc.mcp.obo"
SERVICE_ACCOUNT_ALIAS = "mcpobosv"
PROFILE_NAME = "Minimum Access - Salesforce"
PERM_SET_NAME = "MCP_OBO_Service_Account"
DEFAULT_APP_NAME = "Identity_PoC_OBO_JWT"


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


def get_org_domain(org: str) -> str | None:
    """Get the org's My Domain (e.g., 'mycompany.my.salesforce.com')."""
    result = run(f"sf org display -o {org} --json", parse_json=True)
    if not result:
        return None
    instance_url = result.get("result", {}).get("instanceUrl", "")
    if instance_url:
        return instance_url.replace("https://", "").replace("http://", "")
    return None


def query_profile_id(org: str, profile_name: str) -> str | None:
    """Query the Profile ID by name."""
    soql = f"SELECT Id FROM Profile WHERE Name = '{profile_name}'"
    result = run(f'sf data query --query "{soql}" -o {org} --json', parse_json=True)
    if not result:
        return None
    records = result.get("result", {}).get("records", [])
    if records:
        return records[0]["Id"]
    return None


def query_user(org: str, username: str) -> dict | None:
    """Query a User by username. Returns the record or None."""
    soql = f"SELECT Id, Username, ProfileId, IsActive FROM User WHERE Username = '{username}'"
    result = run(f'sf data query --query "{soql}" -o {org} --json', parse_json=True)
    if not result:
        return None
    records = result.get("result", {}).get("records", [])
    return records[0] if records else None


def create_service_account(org: str, profile_id: str, username: str, email: str) -> str | None:
    """Create the service account user via sf CLI. Returns the new User ID."""
    print(f"  Creating service account: {username}")

    values = (
        f"Username='{username}' "
        f"Email='{email}' "
        f"LastName='Service Account' "
        f"FirstName='MCP OBO' "
        f"Alias='{SERVICE_ACCOUNT_ALIAS}' "
        f"ProfileId='{profile_id}' "
        f"TimeZoneSidKey='Europe/London' "
        f"LocaleSidKey='en_US' "
        f"EmailEncodingKey='UTF-8' "
        f"LanguageLocaleKey='en_US' "
        f"IsActive=true"
    )
    result = run(
        f'sf data create record --sobject User --values "{values}" -o {org} --json',
        parse_json=True,
    )
    if not result or not result.get("result", {}).get("id"):
        print("  ERROR: Failed to create service account")
        return None

    user_id = result["result"]["id"]
    print(f"  Created user: {user_id}")
    return user_id


def assign_permission_set(org: str, user_id: str, username: str) -> bool:
    """Assign the MCP_OBO_Service_Account Permission Set to the user.

    Uses the REST API to create a PermissionSetAssignment record.
    """
    import urllib.request
    import urllib.error

    # Find the Permission Set
    soql = f"SELECT Id FROM PermissionSet WHERE Name = '{PERM_SET_NAME}' AND IsOwnedByProfile = false"
    result = run(f'sf data query --query "{soql}" -o {org} --json', parse_json=True)
    if not result:
        print(f"  WARNING: Permission Set '{PERM_SET_NAME}' not found")
        print("  Run setup-sf-obo-eca.py first to deploy it")
        return False
    records = result.get("result", {}).get("records", [])
    if not records:
        print(f"  WARNING: Permission Set '{PERM_SET_NAME}' not found")
        print("  Run setup-sf-obo-eca.py first to deploy it")
        return False
    ps_id = records[0]["Id"]
    print(f"  Found Permission Set: {PERM_SET_NAME} ({ps_id})")

    # Get access token and instance URL for REST API
    org_info = run(f"sf org display -o {org} --json", parse_json=True)
    if not org_info:
        print("  ERROR: Could not get org info")
        return False
    result_data = org_info.get("result", {})
    access_token = result_data.get("accessToken")
    instance_url = result_data.get("instanceUrl")
    if not access_token or not instance_url:
        print("  ERROR: Could not get access token or instance URL")
        return False

    # Create PermissionSetAssignment
    req = urllib.request.Request(
        f"{instance_url}/services/data/v62.0/sobjects/PermissionSetAssignment",
        data=json.dumps({
            "AssigneeId": user_id,
            "PermissionSetId": ps_id,
        }).encode(),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            if result.get("success"):
                print(f"  Assigned Permission Set to {username}")
                return True
            else:
                print(f"  FAILED to assign: {result}")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "DUPLICATE_VALUE" in body:
            print(f"  Permission Set already assigned to {username}")
            return True
        else:
            print(f"  FAILED to assign: {body[:200]}")
            return False


def preauthorize_for_app(org: str, app_name: str) -> bool:
    """Pre-authorize the Permission Set for the Connected App via SetupEntityAccess.

    This ensures the service account (via its Permission Set) is allowed to
    use the Connected App for JWT Bearer token exchange.
    """
    import urllib.request
    import urllib.error

    org_info = run(f"sf org display -o {org} --json", parse_json=True)
    if not org_info:
        print("  ERROR: Could not get org info")
        return False
    result_data = org_info.get("result", {})
    access_token = result_data.get("accessToken")
    instance_url = result_data.get("instanceUrl")

    # Get ConnectedApplication ID (via Tooling API)
    tooling_result = run(
        f"sf data query -o {org} -t -q "
        f"\"SELECT Id FROM ConnectedApplication WHERE DeveloperName='{app_name}'\" "
        f"--json",
        parse_json=True,
    )
    if not tooling_result:
        print(f"  WARNING: Connected App '{app_name}' not found")
        print("  Run setup-sf-obo-eca.py first to deploy it")
        return False
    ca_records = tooling_result.get("result", {}).get("records", [])
    if not ca_records:
        print(f"  WARNING: Connected App '{app_name}' not found")
        print("  Run setup-sf-obo-eca.py first to deploy it")
        return False
    connected_app_id = ca_records[0]["Id"]

    # Get Permission Set ID
    soql = f"SELECT Id FROM PermissionSet WHERE Name = '{PERM_SET_NAME}' AND IsOwnedByProfile = false"
    ps_result = run(f'sf data query --query "{soql}" -o {org} --json', parse_json=True)
    if not ps_result:
        print(f"  WARNING: Permission Set '{PERM_SET_NAME}' not found")
        return False
    ps_records = ps_result.get("result", {}).get("records", [])
    if not ps_records:
        print(f"  WARNING: Permission Set '{PERM_SET_NAME}' not found")
        return False
    ps_id = ps_records[0]["Id"]

    # Create SetupEntityAccess
    req = urllib.request.Request(
        f"{instance_url}/services/data/v62.0/sobjects/SetupEntityAccess",
        data=json.dumps({
            "ParentId": ps_id,
            "SetupEntityId": connected_app_id,
        }).encode(),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            if result.get("success"):
                print(f"  Pre-authorized Permission Set for Connected App '{app_name}'")
                return True
            else:
                print(f"  FAILED: {result}")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "DUPLICATE_VALUE" in body:
            print(f"  Permission Set already authorized for Connected App")
            return True
        else:
            print(f"  FAILED: {body[:200]}")
            return False


def cleanup(org: str, username: str):
    """Deactivate the service account (can't delete users in SF)."""
    print("\n--- Cleanup ---")

    user = query_user(org, username)
    if user:
        user_id = user["Id"]
        if not user.get("IsActive", True):
            print(f"  User {username} is already inactive")
            return
        print(f"  Deactivating user {username} ({user_id})...")
        run(
            f'sf data update record --sobject User --record-id {user_id} '
            f'--values "IsActive=false" -o {org} --json',
        )
        print("  User deactivated")
    else:
        print(f"  User {username} not found — nothing to clean up")

    print("  Cleanup complete")


def main():
    parser = argparse.ArgumentParser(
        description="Create a dedicated Salesforce service account for OBO (JWT Bearer) flow. "
        "Creates a user with Minimum Access profile and assigns the "
        "MCP_OBO_Service_Account Permission Set."
    )
    parser.add_argument(
        "--org", required=True,
        help="Salesforce org alias (as authenticated with 'sf org login web')",
    )
    parser.add_argument(
        "--email", required=True,
        help="Email address for the service account (receives any SF notifications)",
    )
    parser.add_argument(
        "--username", default=None,
        help="Username for service account (default: svc.mcp.obo@<org-domain>)",
    )
    parser.add_argument(
        "--app-name", default=DEFAULT_APP_NAME,
        help=f"Connected App developer name for pre-authorization (default: {DEFAULT_APP_NAME})",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Deactivate the service account instead of creating it",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Salesforce OBO Service Account Setup")
    print("  JWT Bearer Flow — Dedicated Service User")
    print("=" * 60)

    # Determine username
    domain = get_org_domain(args.org)
    if not domain:
        print("\nERROR: Could not determine org domain. Is the org authenticated?")
        print(f"  Run: sf org login web --alias {args.org}")
        sys.exit(1)

    username = args.username or f"{DEFAULT_USERNAME_PREFIX}@{domain}"
    print(f"\n  Org domain:        {domain}")
    print(f"  Service account:   {username}")
    print(f"  Email:             {args.email}")
    print(f"  Profile:           {PROFILE_NAME}")
    print(f"  Permission Set:    {PERM_SET_NAME}")
    print(f"  Connected App:     {args.app_name}")

    if args.cleanup:
        cleanup(args.org, username)
        return

    # Step 1: Resolve profile
    print("\n--- Step 1: Resolve profile ---")
    profile_id = query_profile_id(args.org, PROFILE_NAME)
    if not profile_id:
        print(f"  ERROR: Profile '{PROFILE_NAME}' not found")
        print("  This is a standard Salesforce profile — verify the org is accessible")
        sys.exit(1)
    print(f"  Profile '{PROFILE_NAME}': {profile_id}")

    # Step 2: Create service account user
    print("\n--- Step 2: Create service account ---")
    existing_user = query_user(args.org, username)
    if existing_user:
        user_id = existing_user["Id"]
        is_active = existing_user.get("IsActive", False)
        print(f"  User '{username}' already exists: {user_id}")
        if not is_active:
            print("  User is inactive — reactivating...")
            run(
                f'sf data update record --sobject User --record-id {user_id} '
                f'--values "IsActive=true" -o {args.org} --json',
            )
            print("  User reactivated")
        # Ensure correct profile
        if existing_user.get("ProfileId") != profile_id:
            print(f"  Updating profile to '{PROFILE_NAME}'...")
            run(
                f'sf data update record --sobject User --record-id {user_id} '
                f'--values "ProfileId=\'{profile_id}\'" -o {args.org} --json',
            )
    else:
        user_id = create_service_account(args.org, profile_id, username, args.email)
        if not user_id:
            sys.exit(1)

    # Step 3: Assign Permission Set
    print("\n--- Step 3: Assign Permission Set ---")
    assign_permission_set(args.org, user_id, username)

    # Step 4: Pre-authorize for Connected App
    print("\n--- Step 4: Pre-authorize for Connected App ---")
    preauthorize_for_app(args.org, args.app_name)

    # Summary
    print()
    print("=" * 60)
    print("  Service Account Setup Complete!")
    print("=" * 60)
    print()
    print("  Service Account Details:")
    print(f"    Username:        {username}")
    print(f"    Profile:         {PROFILE_NAME}")
    print(f"    Permission Set:  {PERM_SET_NAME} (ApiEnabled + ViewAllUsers)")
    print(f"    Connected App:   {args.app_name}")
    print()
    print("  Next steps:")
    print(f'    azd env set SF_SERVICE_ACCOUNT_USERNAME "{username}"')
    print("    azd up")
    print()
    print("  To clean up:")
    print(f"    python scripts/setup-sf-service-account.py --org {args.org} --email {args.email} --cleanup")


if __name__ == "__main__":
    main()
