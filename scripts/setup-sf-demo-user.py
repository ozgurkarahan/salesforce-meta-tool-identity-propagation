"""Set up Salesforce demo user for identity propagation demo.

Automates the Salesforce org setup for demonstrating that an AI agent
inherits (and is limited by) the signed-in user's permissions:
1. Creates a custom profile "Standard User - No Delete" (no Account delete)
2. Creates a demo user assigned to that profile
3. Resets the demo user's password (SF sends reset email)
4. Creates sample test data (Accounts + Contacts) for the demo

Prerequisites:
- Salesforce CLI (`sf`) installed and authenticated to the target org
  Run: sf org login web --alias <alias>

Usage:
    python scripts/setup-sf-demo-user.py --org <alias> --email <your-email>
    python scripts/setup-sf-demo-user.py --org <alias> --email <your-email> --cleanup
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PROFILE_NAME = "Standard User - No Delete"
DEMO_USER_ALIAS = "demondel"


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
        # Extract domain from https://mycompany.my.salesforce.com
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


def create_no_delete_profile(work_dir: str) -> str:
    """Generate a custom profile that denies Account delete permission.

    Creates the profile XML from scratch (avoids Metadata API retrieve of
    standard profiles, which uses internal names like 'Standard' and is fragile).
    The profile inherits standard permissions from the Salesforce user license;
    we only override Account objectPermissions to deny delete.
    """
    _init_sfdx_project(work_dir)
    print(f"  Generating '{PROFILE_NAME}' profile metadata...")

    # Minimal custom profile with Account delete denied.
    # Other object permissions inherit from the Salesforce user license.
    profile_xml = """\
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
    target_dir = os.path.join(work_dir, "force-app", "main", "default", "profiles")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, f"{PROFILE_NAME}.profile-meta.xml")
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(profile_xml)

    print(f"  Created: {target_path}")
    return target_path


def deploy_profile(org: str, work_dir: str) -> bool:
    """Deploy the custom profile to Salesforce."""
    _init_sfdx_project(work_dir)
    print(f"  Deploying '{PROFILE_NAME}' profile...")
    result = run(
        f'sf project deploy start -o {org} --source-dir force-app',
        cwd=work_dir,
    )
    if result is None:
        print("  ERROR: Failed to deploy profile")
        return False
    print("  Profile deployed successfully")
    return True


def create_demo_user(org: str, profile_id: str, username: str, email: str) -> str | None:
    """Create the demo user via sf CLI. Returns the new User ID."""
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
        f'sf data create record --sobject User --values "{values}" -o {org} --json',
        parse_json=True,
    )
    if not result or not result.get("result", {}).get("id"):
        print("  ERROR: Failed to create demo user")
        return None

    user_id = result["result"]["id"]
    print(f"  Created user: {user_id}")
    return user_id


def reset_user_password(org: str, user_id: str) -> bool:
    """Reset demo user's password. Salesforce sends a password reset email."""
    print("  Resetting demo user password (SF will send reset email)...")
    result = run(
        f'sf org generate password --sobject User --on-behalf-of {user_id} -o {org} --json',
        parse_json=True,
    )
    if result and result.get("result"):
        password = result["result"].get("password", "(check email)")
        print(f"  Password set: {password}")
        return True
    # Fallback: password generation might not be allowed; SF sends reset email
    print("  NOTE: Password generation may not be supported. User will receive a reset email.")
    return True


def create_test_data(org: str) -> list[dict]:
    """Create sample Accounts and Contacts for the demo."""
    print("  Creating test data...")
    created = []

    accounts = [
        {"Name": "Acme Corporation", "Industry": "Technology"},
        {"Name": "Global Industries", "Industry": "Manufacturing"},
        {"Name": "Pinnacle Consulting", "Industry": "Consulting"},
    ]

    for acct in accounts:
        # Check if account already exists
        soql = f"SELECT Id FROM Account WHERE Name = '{acct['Name']}'"
        existing = run(f'sf data query --query "{soql}" -o {org} --json', parse_json=True)
        records = (existing or {}).get("result", {}).get("records", [])
        if records:
            acct_id = records[0]["Id"]
            print(f"  Account '{acct['Name']}' already exists: {acct_id}")
        else:
            values = " ".join(f"{k}='{v}'" for k, v in acct.items())
            result = run(
                f'sf data create record --sobject Account --values "{values}" -o {org} --json',
                parse_json=True,
            )
            if result and result.get("result", {}).get("id"):
                acct_id = result["result"]["id"]
                print(f"  Created Account '{acct['Name']}': {acct_id}")
            else:
                print(f"  ERROR: Failed to create Account '{acct['Name']}'")
                continue
        created.append({"type": "Account", "name": acct["Name"], "id": acct_id})

    # Create Contacts linked to the first account
    if created:
        acme_id = created[0]["id"]
        contacts = [
            {"FirstName": "Jane", "LastName": "Smith", "Email": "jane.smith@acmecorp.example.com"},
            {"FirstName": "Bob", "LastName": "Johnson", "Email": "bob.johnson@acmecorp.example.com"},
        ]
        for contact in contacts:
            full_name = f"{contact['FirstName']} {contact['LastName']}"
            soql = (
                f"SELECT Id FROM Contact WHERE FirstName = '{contact['FirstName']}' "
                f"AND LastName = '{contact['LastName']}' AND AccountId = '{acme_id}'"
            )
            existing = run(f'sf data query --query "{soql}" -o {org} --json', parse_json=True)
            records = (existing or {}).get("result", {}).get("records", [])
            if records:
                print(f"  Contact '{full_name}' already exists: {records[0]['Id']}")
                created.append({"type": "Contact", "name": full_name, "id": records[0]["Id"]})
            else:
                values = " ".join(f"{k}='{v}'" for k, v in contact.items())
                values += f" AccountId='{acme_id}'"
                result = run(
                    f'sf data create record --sobject Contact --values "{values}" -o {org} --json',
                    parse_json=True,
                )
                if result and result.get("result", {}).get("id"):
                    cid = result["result"]["id"]
                    print(f"  Created Contact '{full_name}': {cid}")
                    created.append({"type": "Contact", "name": full_name, "id": cid})
                else:
                    print(f"  ERROR: Failed to create Contact '{full_name}'")

    return created


def cleanup(org: str, username: str):
    """Remove demo user, profile, and test data."""
    print("\n--- Cleanup ---")

    # Deactivate demo user (can't delete users in SF, only deactivate)
    user = query_user(org, username)
    if user:
        user_id = user["Id"]
        print(f"  Deactivating user {username} ({user_id})...")
        run(
            f'sf data update record --sobject User --record-id {user_id} '
            f'--values "IsActive=false" -o {org} --json',
        )
        print("  User deactivated")
    else:
        print(f"  User {username} not found — skipping")

    # Delete test data
    for name in ["Pinnacle Consulting", "Global Industries", "Acme Corporation"]:
        soql = f"SELECT Id FROM Account WHERE Name = '{name}'"
        result = run(f'sf data query --query "{soql}" -o {org} --json', parse_json=True)
        records = (result or {}).get("result", {}).get("records", [])
        for rec in records:
            print(f"  Deleting Account '{name}' ({rec['Id']})...")
            run(f'sf data delete record --sobject Account --record-id {rec["Id"]} -o {org}')

    # Note: Profile deletion via Metadata API is complex and usually not needed
    print(f"  NOTE: Profile '{PROFILE_NAME}' not deleted (remove manually if needed)")
    print("  Cleanup complete")


def main():
    parser = argparse.ArgumentParser(
        description="Set up Salesforce demo user for identity propagation demo."
    )
    parser.add_argument(
        "--org", required=True,
        help="Salesforce org alias (as authenticated with 'sf org login web')",
    )
    parser.add_argument(
        "--email", required=True,
        help="Email address for the demo user (receives password reset)",
    )
    parser.add_argument(
        "--username", default=None,
        help="Username for demo user (default: demo.nodelete@<org-domain>)",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Remove demo user and test data instead of creating them",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Salesforce Demo User Setup")
    print("  Identity Propagation Demo")
    print("=" * 60)

    # Determine username
    domain = get_org_domain(args.org)
    if not domain:
        print("\nERROR: Could not determine org domain. Is the org authenticated?")
        print(f"  Run: sf org login web --alias {args.org}")
        sys.exit(1)

    username = args.username or f"demo.nodelete@{domain}"
    print(f"\n  Org domain:  {domain}")
    print(f"  Demo user:   {username}")
    print(f"  Email:       {args.email}")
    print(f"  Profile:     {PROFILE_NAME}")

    if args.cleanup:
        cleanup(args.org, username)
        return

    # Step 1: Create custom profile
    print("\n--- Step 1: Create custom profile ---")
    profile_id = query_profile_id(args.org, PROFILE_NAME)
    if profile_id:
        print(f"  Profile '{PROFILE_NAME}' already exists: {profile_id}")
    else:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work_dir:
            create_no_delete_profile(work_dir)
            if not deploy_profile(args.org, work_dir):
                sys.exit(1)

        profile_id = query_profile_id(args.org, PROFILE_NAME)
        if not profile_id:
            print("  ERROR: Profile deployed but ID not found")
            sys.exit(1)
        print(f"  Profile ID: {profile_id}")

    # Step 2: Create demo user
    print("\n--- Step 2: Create demo user ---")
    existing_user = query_user(args.org, username)
    if existing_user:
        user_id = existing_user["Id"]
        print(f"  User '{username}' already exists: {user_id}")
        # Ensure user has the correct profile
        if existing_user.get("ProfileId") != profile_id:
            print(f"  Updating profile to '{PROFILE_NAME}'...")
            run(
                f'sf data update record --sobject User --record-id {user_id} '
                f'--values "ProfileId=\'{profile_id}\'" -o {args.org} --json',
            )
    else:
        user_id = create_demo_user(args.org, profile_id, username, args.email)
        if not user_id:
            sys.exit(1)

    # Step 3: Reset password
    print("\n--- Step 3: Reset password ---")
    reset_user_password(args.org, user_id)

    # Step 4: Create test data
    print("\n--- Step 4: Create test data ---")
    test_data = create_test_data(args.org)

    # Summary
    print()
    print("=" * 60)
    print("  Setup Complete!")
    print("=" * 60)
    print()
    print("  Demo User Details:")
    print(f"    Username:     {username}")
    print(f"    Profile:      {PROFILE_NAME}")
    print(f"    Restriction:  Cannot delete Account records")
    print(f"    Can do:       Read, Create, Edit Accounts + standard objects")
    print(f"    Cannot do:    Delete Accounts")
    print()
    print("  Test Data:")
    for item in test_data:
        print(f"    {item['type']}: {item['name']} ({item['id']})")
    print()
    print("  Demo Flow:")
    print("    1. Sign in as demo user in Chat App")
    print('    2. Ask: "List my Salesforce accounts" -> succeeds')
    print('    3. Ask: "Create a test account called Demo Corp" -> succeeds')
    print('    4. Ask: "Delete the Demo Corp account" -> FAILS (permission denied)')
    print()
    print("  To clean up:")
    print(f"    python scripts/setup-sf-demo-user.py --org {args.org} --email {args.email} --cleanup")


if __name__ == "__main__":
    main()
