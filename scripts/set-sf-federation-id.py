"""Set FederationIdentifier on Salesforce users to their Azure AD oid.

Enables the OBO JWT Bearer flow where APIM uses the Azure AD object ID (oid)
as the `sub` claim in the JWT assertion, and Salesforce matches it to the
user's FederationIdentifier field.

For each active SF user:
1. Look up the corresponding Azure AD user by email/UPN
2. Get their Azure AD object ID (oid)
3. Set SF User.FederationIdentifier = oid

Prerequisites:
- Salesforce CLI (sf) installed and authenticated to the target org
- Azure CLI (az) installed and signed in to the correct tenant

Usage:
    python scripts/set-sf-federation-id.py --org <alias>
    python scripts/set-sf-federation-id.py --org <alias> --dry-run
    python scripts/set-sf-federation-id.py --org <alias> --user alice@contoso.com --user bob@contoso.com
"""

import argparse
import json
import os
import subprocess
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def run(cmd: str, parse_json: bool = False):
    """Run a shell command and return stdout."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
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


def query_sf_users(org: str, user_emails: list[str] | None = None) -> list[dict]:
    """Query active standard users from Salesforce."""
    soql = (
        "SELECT Id, Username, Email, FederationIdentifier "
        "FROM User "
        "WHERE IsActive = true AND UserType = 'Standard'"
    )
    if user_emails:
        # Filter to specific users by email
        email_list = "', '".join(user_emails)
        soql += f" AND (Email IN ('{email_list}') OR Username IN ('{email_list}'))"

    result = run(
        f'sf data query -o {org} -q "{soql}" --json',
        parse_json=True,
    )
    if result is None:
        print("  ERROR: Failed to query Salesforce users")
        print(f"  Verify the sf CLI is authenticated: sf org display -o {org}")
        return []

    records = result.get("result", {}).get("records", [])
    return records


def lookup_azure_ad_oid(email: str) -> str | None:
    """Look up the Azure AD object ID for a user by email/UPN."""
    result = run(
        f'az ad user show --id "{email}" --query id -o tsv',
    )
    return result


def update_federation_id(org: str, user_id: str, oid: str) -> bool:
    """Set FederationIdentifier on a Salesforce user."""
    result = run(
        f'sf data update record -o {org} -s User -i {user_id} '
        f'-v "FederationIdentifier={oid}"',
    )
    return result is not None


def main():
    parser = argparse.ArgumentParser(
        description="Set FederationIdentifier on Salesforce users to their "
        "Azure AD object ID (oid). Enables OBO JWT Bearer flow where APIM "
        "uses the oid as the JWT sub claim."
    )
    parser.add_argument(
        "--org", required=True,
        help="Salesforce org alias (as authenticated with 'sf org login web')",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without making any updates",
    )
    parser.add_argument(
        "--user", action="append", dest="users", metavar="EMAIL",
        help="Target specific user(s) by email (can be repeated)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Set Salesforce FederationIdentifier from Azure AD")
    print("=" * 60)

    if args.dry_run:
        print("\n  ** DRY RUN -- no changes will be made **")

    # Step 1: Query SF users
    print("\n--- Step 1: Query Salesforce users ---")
    users = query_sf_users(args.org, args.users)

    if not users:
        print("  No matching users found")
        sys.exit(0)

    print(f"  Found {len(users)} user(s)")

    # Step 2: Match to Azure AD and update
    print("\n--- Step 2: Match Azure AD and update FederationIdentifier ---")
    updated = 0
    skipped = 0
    failed = 0
    not_found = 0

    for user in users:
        user_id = user.get("Id", "")
        username = user.get("Username", "")
        email = user.get("Email", "")
        current_fed_id = user.get("FederationIdentifier") or ""

        # Use email first, fall back to username for Azure AD lookup
        lookup_id = email or username
        if not lookup_id:
            print(f"  SKIP: User {user_id} has no email or username")
            skipped += 1
            continue

        print(f"\n  User: {lookup_id}")
        print(f"    SF Id:          {user_id}")
        print(f"    Current FedId:  {current_fed_id or '(empty)'}")

        # Look up Azure AD oid
        oid = lookup_azure_ad_oid(lookup_id)

        # If email lookup failed and username differs, try username
        if oid is None and username and username != email:
            print(f"    Email not found in Azure AD, trying username: {username}")
            oid = lookup_azure_ad_oid(username)

        if oid is None:
            print(f"    NOT FOUND in Azure AD -- skipping")
            not_found += 1
            continue

        print(f"    Azure AD oid:   {oid}")

        # Check if already set correctly
        if current_fed_id == oid:
            print(f"    Already set correctly -- skipping")
            skipped += 1
            continue

        # Update
        if args.dry_run:
            print(f"    WOULD SET FederationIdentifier = {oid}")
            updated += 1
        else:
            if update_federation_id(args.org, user_id, oid):
                print(f"    UPDATED FederationIdentifier = {oid}")
                updated += 1
            else:
                print(f"    FAILED to update")
                failed += 1

    # Summary
    print()
    print("=" * 60)
    action = "Would update" if args.dry_run else "Updated"
    print(f"  {action}: {updated}  |  Skipped: {skipped}  |  "
          f"Not found: {not_found}  |  Failed: {failed}")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
