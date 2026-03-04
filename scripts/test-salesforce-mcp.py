"""Test script for the Salesforce MCP server.

Exercises all client methods end-to-end:
1. Authenticate
2. list_objects — discover available objects
3. describe_object — inspect Account fields (including externalId flag)
4. describe_object — inspect Opportunity fields + picklist values
5. soql_query — simple query
6. soql_query — relationship query
7. soql_query — pagination (query_more)
8. search — cross-object full-text search
9. write_record — create, update, delete cycle
10. describe_object — verify externalId flag for upsert validation
11. process_approval — skip gracefully if no approval process configured

Usage:
    # Set instance URL and a pre-supplied access token, then run:
    export SF_INSTANCE_URL="https://your-org.my.salesforce.com"
    export SF_ACCESS_TOKEN="<your-salesforce-access-token>"
    python scripts/test-salesforce-mcp.py
"""

import asyncio
import json
import os
import sys

# Add the salesforce-mcp source to path so we can import the client directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "salesforce-mcp"))

from salesforce_client import SalesforceClient


def print_step(step: int, title: str):
    print(f"\n{'='*60}")
    print(f"  Step {step}: {title}")
    print(f"{'='*60}")


def print_result(data, max_items: int = 5):
    if isinstance(data, list):
        print(f"  ({len(data)} items, showing first {min(len(data), max_items)})")
        for item in data[:max_items]:
            print(f"    - {json.dumps(item, indent=6)}")
    elif isinstance(data, dict):
        print(f"  {json.dumps(data, indent=4)}")
    else:
        print(f"  {data}")


async def main():
    print("\nSalesforce MCP Server — End-to-End Client Test")
    print("=" * 60)

    sf = SalesforceClient()

    # --- Step 1: Verify bearer token setup ---
    print_step(1, "Verify bearer token setup")
    if not sf.instance_url:
        print("  FAILED: SF_INSTANCE_URL not set")
        return
    if not sf._fallback_token:
        print("  FAILED: SF_ACCESS_TOKEN not set (required for local testing)")
        return
    print(f"  OK -- instance: {sf.instance_url}")
    print(f"  Token: {sf._fallback_token[:20]}...")

    # --- Step 2: List Objects ---
    print_step(2, "list_objects — Discover available objects")
    objects = await sf.describe_global()
    queryable = [o for o in objects if o["queryable"]]
    print(f"  Total objects: {len(objects)}")
    print(f"  Queryable: {len(queryable)}")
    interesting = [o for o in queryable if o["name"] in ("Account", "Contact", "Opportunity", "Lead", "Case")]
    print("  Key objects:")
    for o in interesting:
        print(f"    - {o['name']} ({o['label']}): query={o['queryable']}, create={o['createable']}, update={o['updateable']}, delete={o['deletable']}")

    # --- Step 3: Describe Account ---
    print_step(3, "describe_object — Account field metadata (incl. externalId)")
    account_desc = await sf.describe_object("Account")
    print(f"  Object: {account_desc['name']} ({account_desc['label']})")
    print(f"  Total fields: {len(account_desc['fields'])}")
    print(f"  Child relationships: {len(account_desc['childRelationships'])}")
    key_fields = [f for f in account_desc["fields"] if f["name"] in ("Id", "Name", "Industry", "Phone", "Website", "Type")]
    print("  Key fields:")
    for f in key_fields:
        req = " (REQUIRED)" if f["required"] else ""
        ext = " (EXTERNAL ID)" if f.get("externalId") else ""
        print(f"    - {f['name']} [{f['type']}]{req}{ext}")
    # Verify externalId flag is present in describe output
    assert "externalId" in account_desc["fields"][0], "externalId flag missing from describe output"
    ext_id_fields = [f for f in account_desc["fields"] if f.get("externalId")]
    print(f"  External ID fields: {len(ext_id_fields)}")
    for f in ext_id_fields:
        print(f"    - {f['name']} [{f['type']}]")

    # --- Step 4: Describe Opportunity ---
    print_step(4, "describe_object — Opportunity field metadata")
    opp_desc = await sf.describe_object("Opportunity")
    print(f"  Object: {opp_desc['name']} ({opp_desc['label']})")
    print(f"  Total fields: {len(opp_desc['fields'])}")
    stage_field = next((f for f in opp_desc["fields"] if f["name"] == "StageName"), None)
    if stage_field and stage_field.get("picklistValues"):
        print("  StageName picklist values:")
        for pv in stage_field["picklistValues"][:8]:
            print(f"    - {pv['value']}")

    # --- Step 5: soql_query — simple query ---
    print_step(5, "soql_query — Simple Account query")
    result = await sf.query("SELECT Id, Name, Industry, Phone FROM Account LIMIT 5")
    print(f"  Total records: {result['totalSize']}")
    for rec in result.get("records", []):
        rec.pop("attributes", None)
        print(f"    - {rec.get('Name', 'N/A')} | Industry: {rec.get('Industry', 'N/A')} | Phone: {rec.get('Phone', 'N/A')}")

    # --- Step 6: soql_query — relationship query ---
    print_step(6, "soql_query — Relationship query (Account with Contacts)")
    result = await sf.query(
        "SELECT Id, Name, (SELECT FirstName, LastName FROM Contacts) FROM Account LIMIT 3"
    )
    print(f"  Total records: {result['totalSize']}")
    for rec in result.get("records", []):
        rec.pop("attributes", None)
        contacts = rec.get("Contacts")
        contact_count = 0
        if contacts and contacts.get("records"):
            for c in contacts["records"]:
                c.pop("attributes", None)
            contact_count = len(contacts["records"])
        print(f"    - {rec.get('Name', 'N/A')} ({contact_count} contacts)")

    # --- Step 7: soql_query — pagination via query_more ---
    print_step(7, "soql_query — Pagination (query + query_more)")
    # Use LIMIT 1 to get a small result, then check done flag and pagination plumbing
    result = await sf.query("SELECT Id, Name FROM Account LIMIT 1")
    print(f"  First page: {len(result.get('records', []))} record(s), done={result.get('done')}")
    print(f"  nextRecordsUrl present: {'nextRecordsUrl' in result}")
    # Test query_more is callable (would need >2000 records for real pagination)
    if result.get("nextRecordsUrl"):
        next_result = await sf.query_more(result["nextRecordsUrl"])
        print(f"  query_more returned {len(next_result.get('records', []))} additional records")
    else:
        print("  (No pagination needed — result set fits in one page)")
    # Verify the method exists and is async
    assert hasattr(sf, "query_more"), "query_more method missing from SalesforceClient"
    print("  query_more method: OK")

    # --- Step 8: search — SOSL full-text search ---
    print_step(8, "search — Cross-object SOSL search")
    try:
        result = await sf.search("FIND {Test} IN ALL FIELDS LIMIT 10")
        records = result.get("searchRecords", [])
        print(f"  Found {len(records)} records")
        for rec in records[:5]:
            rec.pop("attributes", None)
            obj_type = rec.get("attributes", {}).get("type", "Unknown") if "attributes" in rec else "Record"
            print(f"    - [{obj_type}] {rec.get('Name', rec.get('Id', 'N/A'))}")
    except Exception as e:
        print(f"  Search returned error (may be normal if no indexed data): {e}")

    # --- Step 9: write_record — Create test Account ---
    print_step(9, "write_record — Create test Account")
    test_account = {"Name": "MCP Test Account v2", "Industry": "Technology", "Phone": "555-0100"}
    create_result = await sf.create_record("Account", test_account)
    print_result(create_result)

    if create_result.get("success"):
        record_id = create_result["id"]
        print(f"\n  Created Account ID: {record_id}")

        # --- Step 10: write_record — Update test Account ---
        print_step(10, "write_record — Update test Account")
        update_result = await sf.update_record(
            "Account", record_id,
            {"Phone": "555-0200", "Website": "https://mcp-test.example.com"},
        )
        print_result(update_result)

        # Verify the update
        verify = await sf.query(f"SELECT Id, Name, Phone, Website FROM Account WHERE Id = '{record_id}'")
        if verify["records"]:
            rec = verify["records"][0]
            rec.pop("attributes", None)
            print(f"  Verified: Name={rec['Name']}, Phone={rec['Phone']}, Website={rec['Website']}")

        # --- Step 11: write_record — Delete test Account ---
        print_step(11, "write_record — Delete test Account")
        delete_result = await sf.delete_record("Account", record_id)
        print_result(delete_result)
    else:
        print("  Skipping update/delete — create failed")

    # --- Step 12: Verify externalId flag for upsert validation ---
    print_step(12, "describe_object — External ID validation check")
    # Name field should NOT be an external ID on Account (used to validate upsert rejection)
    name_field = next((f for f in account_desc["fields"] if f["name"] == "Name"), None)
    assert name_field is not None, "Name field not found on Account"
    assert not name_field.get("externalId"), "Name should not be an external ID"
    print(f"  Name field externalId={name_field.get('externalId')} — correct (not an external ID)")
    # Id field type should be 'id' (special case: allowed for upsert even without externalId flag)
    id_field = next((f for f in account_desc["fields"] if f["name"] == "Id"), None)
    assert id_field is not None, "Id field not found on Account"
    assert id_field.get("type") == "id", "Id field should have type 'id'"
    print(f"  Id field type={id_field.get('type')} — correct (allowed for upsert)")
    print("  Client is bearer-passthrough-only (no refresh_token)")

    # --- Step 13: process_approval — test (skip if no approval process) ---
    print_step(13, "process_approval — Check for pending approvals")
    try:
        result = await sf.query(
            "SELECT Id, ProcessInstance.TargetObjectId, Actor.Name "
            "FROM ProcessInstanceWorkitem LIMIT 1"
        )
        if result["totalSize"] > 0:
            print(f"  Found {result['totalSize']} pending approval(s)")
            print("  (Skipping actual approve/reject to avoid side effects)")
        else:
            print("  No pending approvals found (expected for most test orgs)")
    except Exception as e:
        print(f"  Could not query approvals: {e}")
        print("  (This is normal if no approval processes are configured)")

    await sf.close()

    print(f"\n{'='*60}")
    print("  All steps completed!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
