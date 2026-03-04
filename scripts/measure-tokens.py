"""Measure token costs of MCP tool definitions and results (before vs after optimization).
Uses character-based estimation (~3.7 chars/token for JSON/English).
"""
import json

def estimate_tokens(text_or_len, chars_per_token=3.7):
    """Rough token estimate. GPT-4o averages ~3.5-4 chars/token."""
    n = text_or_len if isinstance(text_or_len, int) else len(text_or_len)
    return int(n / chars_per_token)

# ---- BEFORE: old tool descriptions (chars in docstring) ----
old_tool_descriptions = {
    "list_objects": 583,
    "describe_object": 628,
    "soql_query": 870,
    "search_records": 703,
    "write_record": 939,
    "process_approval": 569,
}
old_server_instructions_chars = 870

# ---- AFTER: new tool descriptions (measure from actual docstrings) ----
new_tool_descriptions = {
    "list_objects": len("""List available Salesforce objects with permission flags.

    A typical org has 1000+ objects. Always provide a filter to narrow results
    (e.g., "Account", "Order", "Case"). Without one, only the first 100
    alphabetically are returned. Use `name` (API name) for all subsequent calls.

    Args:
        filter: Case-insensitive filter on object name or label. Strongly recommended.

    Returns:
        JSON array (max 100) with name, label, queryable, createable, updateable, deletable."""),
    "describe_object": len("""Get field metadata for a Salesforce object.

    Args:
        object_name: API name (e.g., Account, Contact, Opportunity). Use `name` from list_objects.
        mode: "slim" (default) -- field names, types, required flags. Use for building queries.
              "full" -- includes picklistValues, referenceTo, childRelationships, externalId.
              Use "full" before create/update/upsert/delete.

    Returns:
        slim: JSON with name and fields (name, type, required).
        full: JSON with fields (name, label, type, required, externalId, picklistValues,
              referenceTo) and childRelationships."""),
    "soql_query": len("""Execute a SOQL query with automatic pagination.

    Use for precise, structured lookups. Use search_records for full-text/fuzzy search.
    SOQL requires explicit field names (no SELECT *).

    Syntax:
        - Parent fields: `Account.Name` (dot notation via referenceTo).
        - Child subqueries: `(SELECT Id FROM Contacts)` (uses relationshipName, not object name).
        - Examples: "SELECT Id, Name FROM Account LIMIT 5",
          "SELECT Id, Account.Name FROM Contact LIMIT 5"

    Args:
        query: Complete SOQL string.
        max_records: Max records to return (default 2000, cap 50000). Auto-paginates.

    Returns:
        JSON with totalSize, records array, done flag. done=false means results were truncated."""),
    "search_records": len("""Full-text search across multiple Salesforce objects (SOSL).

    Use when the target object is unknown or for fuzzy/keyword search.
    Prefer soql_query for exact matches and WHERE clauses.

    Args:
        search_term: Plain text to search for (e.g., "Acme"). Special chars auto-escaped.
        objects: RETURNING clause -- objects and fields to return.
            E.g., "Account(Name, Industry), Contact(FirstName, Email)".
            Omit to search all objects with default fields.
        limit: Max records to return (default 20, max 200).

    Returns:
        JSON with searchRecords array."""),
    "write_record": len("""Create, update, upsert, or delete a Salesforce record.

    Operations:
        create  -- field_values required (include all required fields).
        update  -- record_id + field_values (partial update).
        upsert  -- field_values + external_id_field. External ID value must be in field_values.
        delete  -- record_id only.

    Args:
        object_name: API name (e.g., Account, Contact).
        operation: One of "create", "update", "upsert", "delete".
        field_values: API field names to values. E.g., {"Name": "Acme", "Industry": "Technology"}.
        record_id: 18-char Salesforce record ID. Required for update/delete.
        external_id_field: External ID field for upsert. Must have externalId: true in describe_object.

    Returns:
        JSON with success flag and details (id for create, created flag for upsert)."""),
    "process_approval": len("""Submit, approve, or reject a Salesforce approval request.

    For Approve/Reject, query ProcessInstanceWorkitem first to get the workitem ID.
    The record_id for Submit is the record itself; for Approve/Reject it is the
    ProcessInstanceWorkitem ID (not the record).

    Args:
        action: One of "Submit", "Approve", "Reject".
        record_id: For Submit -- the record ID. For Approve/Reject -- the ProcessInstanceWorkitem ID.
        comments: Optional comments for the approval action.

    Returns:
        JSON with success flag and approval result details."""),
}

new_server_instructions = """\
Salesforce MCP server -- discovers objects and fields dynamically via metadata APIs.

## Workflow
1. **Plan** -- Tell the user what you intend to do before calling tools.
2. **list_objects** -- Find the API name (use `name`, not `label`, for all subsequent calls).
3. **describe_object** -- REQUIRED before create/update/upsert/delete.
   For read queries, skip if you already know the field names, or use mode="slim" if unsure.
4. **Execute** -- soql_query, search_records, write_record, or process_approval.
5. **Summarize** -- Present results in plain language. Do NOT dump raw JSON for large results.

## Conventions
- All API names are PascalCase: Account, OpportunityLineItem, Custom_Field__c.
- Field values use API name as key: {"Name": "Acme", "Industry": "Technology"}.
- Record IDs are 18-character alphanumeric strings.
- Common fields on standard objects: Id, Name, CreatedDate, OwnerId, LastModifiedDate.
  You may query these without calling describe_object first.

## Rules
- Do NOT guess field names -- use describe_object (slim for reads, full for writes).
- On INVALID_FIELD or MALFORMED_QUERY: call describe_object(mode="full"), fix field names, retry.
- ALWAYS confirm with the user before delete or reject operations.
- Always include LIMIT in SOQL unless the user specifically requests all rows.
- Summarize large result sets in plain language -- do not dump raw JSON.

## Error recovery
- **INSUFFICIENT_ACCESS** -- User lacks permission. Explain which object/field and what permission is needed.
- **ENTITY_IS_DELETED** -- Record was deleted. Inform the user.
- **UNABLE_TO_LOCK_ROW** -- Concurrent edit. Wait a moment and retry once.
- On any error, explain the cause in plain language before retrying.
"""
new_server_instructions_chars = len(new_server_instructions)

# ---- Build simulated tool results ----
field_names = [
    "Id", "IsDeleted", "MasterRecordId", "Name", "Type", "RecordTypeId", "ParentId",
    "BillingStreet", "BillingCity", "BillingState", "BillingPostalCode", "BillingCountry",
    "BillingLatitude", "BillingLongitude", "BillingGeocodeAccuracy", "BillingAddress",
    "ShippingStreet", "ShippingCity", "ShippingState", "ShippingPostalCode", "ShippingCountry",
    "ShippingLatitude", "ShippingLongitude", "ShippingGeocodeAccuracy", "ShippingAddress",
    "Phone", "Fax", "AccountNumber", "Website", "PhotoUrl", "Sic", "Industry",
    "AnnualRevenue", "NumberOfEmployees", "Ownership", "TickerSymbol", "Description",
    "Rating", "Site", "OwnerId", "CreatedDate", "CreatedById", "LastModifiedDate",
    "LastModifiedById", "SystemModstamp", "LastActivityDate", "LastViewedDate",
    "LastReferencedDate", "Jigsaw", "JigsawCompanyId", "CleanStatus", "AccountSource",
    "DunsNumber", "Tradestyle", "NaicsCode", "NaicsDesc", "YearStarted", "SicDesc",
    "DandbCompanyId", "OperatingHoursId", "CustomerPriority__c", "SLA__c", "Active__c",
    "NumberofLocations__c", "UpsellOpportunity__c", "SLASerialNumber__c", "SLAExpirationDate__c",
]

# Full describe result (before)
fields_full = []
for fn in field_names:
    f = {"name": fn, "label": fn.replace("__c", "").replace("_", " "), "type": "string",
         "required": fn == "Name", "externalId": False, "picklistValues": None, "referenceTo": None}
    if fn in ("Industry", "Type", "Rating", "Ownership", "AccountSource", "CustomerPriority__c",
              "SLA__c", "Active__c", "UpsellOpportunity__c", "CleanStatus"):
        f["type"] = "picklist"
        f["picklistValues"] = [{"value": "Val1", "label": "Value 1"}, {"value": "Val2", "label": "Value 2"},
                               {"value": "Val3", "label": "Value 3"}]
    if fn in ("OwnerId", "ParentId", "CreatedById", "LastModifiedById", "DandbCompanyId",
              "OperatingHoursId", "RecordTypeId", "MasterRecordId"):
        f["type"] = "reference"
        f["referenceTo"] = ["User"]
    if fn == "Id":
        f["type"] = "id"
    fields_full.append(f)

child_rels = [
    {"childSObject": c, "relationshipName": r, "field": fld}
    for c, r, fld in [
        ("Contact", "Contacts", "AccountId"), ("Opportunity", "Opportunities", "AccountId"),
        ("Case", "Cases", "AccountId"), ("Task", "Tasks", "WhatId"), ("Event", "Events", "WhatId"),
        ("Note", "Notes", "ParentId"), ("Attachment", "Attachments", "ParentId"),
        ("ActivityHistory", "ActivityHistories", "AccountId"), ("Asset", "Assets", "AccountId"),
        ("Contract", "Contracts", "AccountId"), ("Order", "Orders", "AccountId"),
        ("Partner", "Partners", "AccountFromId"), ("AccountContactRole", "AccountContactRoles", "AccountId"),
        ("AccountShare", "Shares", "AccountId"), ("AccountHistory", "Histories", "AccountId"),
    ]
]

describe_full = json.dumps({"name": "Account", "label": "Account", "fields": fields_full, "childRelationships": child_rels})

# Slim describe result (after)
fields_slim = [{"name": f["name"], "type": f["type"], "required": f["required"]} for f in fields_full]
describe_slim = json.dumps({"name": "Account", "fields": fields_slim})

# list_objects result
list_result = json.dumps([
    {"name": "Account", "label": "Account", "queryable": True, "createable": True, "updateable": True, "deletable": True},
    {"name": "AccountCleanInfo", "label": "Account Clean Info", "queryable": True, "createable": False, "updateable": True, "deletable": False},
    {"name": "AccountContactRelation", "label": "Account Contact Relation", "queryable": True, "createable": True, "updateable": True, "deletable": True},
    {"name": "AccountContactRole", "label": "Account Contact Role", "queryable": True, "createable": True, "updateable": True, "deletable": True},
    {"name": "AccountHistory", "label": "Account History", "queryable": True, "createable": False, "updateable": False, "deletable": False},
])

# soql_query result (18 accounts)
records = [
    {"Id": f"001xx00000{i:06d}AAA", "Name": f"Test Account {i}", "Type": "Customer",
     "Industry": "Technology", "Phone": f"555-{i:04d}", "Website": f"https://test{i}.example.com",
     "OwnerId": "005xx000001234AAA", "CreatedDate": "2024-01-15T10:30:00.000+0000"}
    for i in range(18)
]
query_result = json.dumps({"totalSize": 18, "records": records, "done": True})

# ---- Token estimates ----
list_tok = estimate_tokens(list_result)
desc_full_tok = estimate_tokens(describe_full)
desc_slim_tok = estimate_tokens(describe_slim)
query_tok = estimate_tokens(query_result)

param_schemas = 80  # JSON schemas are compact

# Old tool defs
old_total_desc = sum(estimate_tokens(c) for c in old_tool_descriptions.values())
old_instr_tok = estimate_tokens(old_server_instructions_chars)
old_tool_defs = old_total_desc + old_instr_tok + param_schemas

# New tool defs
new_total_desc = sum(estimate_tokens(c) for c in new_tool_descriptions.values())
new_instr_tok = estimate_tokens(new_server_instructions_chars)
new_tool_defs = new_total_desc + new_instr_tok + param_schemas

print("=" * 70)
print("  TOKEN SAVINGS: Before vs After Optimization")
print("=" * 70)

# ---- Tool definitions comparison ----
print("\n--- TOOL DEFINITIONS (per LLM call) ---")
print(f"  {'Component':30s}  {'Before':>7s}  {'After':>7s}  {'Saved':>7s}")
print(f"  {'-'*30:30s}  {'-'*7:>7s}  {'-'*7:>7s}  {'-'*7:>7s}")
for name in old_tool_descriptions:
    old = estimate_tokens(old_tool_descriptions[name])
    new = estimate_tokens(new_tool_descriptions[name])
    print(f"  {name:30s}  {old:>7d}  {new:>7d}  {old - new:>+7d}")
print(f"  {'Server instructions':30s}  {old_instr_tok:>7d}  {new_instr_tok:>7d}  {old_instr_tok - new_instr_tok:>+7d}")
print(f"  {'Param schemas':30s}  {param_schemas:>7d}  {param_schemas:>7d}  {0:>+7d}")
print(f"  {'-'*30:30s}  {'-'*7:>7s}  {'-'*7:>7s}  {'-'*7:>7s}")
print(f"  {'TOTAL per LLM call':30s}  {old_tool_defs:>7d}  {new_tool_defs:>7d}  {old_tool_defs - new_tool_defs:>+7d}")

# ---- Describe result comparison ----
print(f"\n--- DESCRIBE_OBJECT RESULT (Account, {len(field_names)} fields) ---")
print(f"  Full:  {desc_full_tok:>5,d} tokens  ({len(describe_full):>6,d} chars)")
print(f"  Slim:  {desc_slim_tok:>5,d} tokens  ({len(describe_slim):>6,d} chars)")
print(f"  Saved: {desc_full_tok - desc_slim_tok:>5,d} tokens  ({(desc_full_tok - desc_slim_tok) * 100 // desc_full_tok}% smaller)")

# ---- BEFORE: 4-call chain (always calls describe) ----
print(f"\n--- BEFORE: 'list my salesforce accounts' (4 LLM calls) ---")
system_prompt = 200
user_msg = 20
base_old = system_prompt + old_tool_defs + user_msg

steps_old = [
    ("Call 1: Decide first tool",        base_old, 50),
    ("Call 2: + list_objects result",     list_tok, 50),
    ("Call 3: + describe_object (full)",  desc_full_tok, 80),
    ("Call 4: + soql_query result",       query_tok, 200),
]
cumulative = 0
total_old = 0
for name, added, output in steps_old:
    cumulative += added
    call_total = cumulative + output
    total_old += call_total
    print(f"  {name:40s}  {cumulative:>5,d} in + {output:>3d} out = {call_total:>5,d}")
print(f"  {'TOTAL':40s}  {total_old:>20,d} tokens")

# ---- AFTER scenario 1: 4-call chain with slim describe ----
print(f"\n--- AFTER (A+C): slim describe + trimmed descriptions (4 calls) ---")
base_new = system_prompt + new_tool_defs + user_msg

steps_new = [
    ("Call 1: Decide first tool",        base_new, 50),
    ("Call 2: + list_objects result",     list_tok, 50),
    ("Call 3: + describe_object (slim)",  desc_slim_tok, 80),
    ("Call 4: + soql_query result",       query_tok, 200),
]
cumulative = 0
total_new_ac = 0
for name, added, output in steps_new:
    cumulative += added
    call_total = cumulative + output
    total_new_ac += call_total
    print(f"  {name:40s}  {cumulative:>5,d} in + {output:>3d} out = {call_total:>5,d}")
print(f"  {'TOTAL':40s}  {total_new_ac:>20,d} tokens")
savings_ac = total_old - total_new_ac
print(f"  Saved: {savings_ac:,d} tokens ({savings_ac * 100 // total_old}% reduction)")

# ---- AFTER scenario 2: 3-call chain (skip describe for reads) ----
print(f"\n--- AFTER (A+B+C): skip describe for reads (3 calls) ---")
steps_skip = [
    ("Call 1: Decide first tool",        base_new, 50),
    ("Call 2: + list_objects result",     list_tok, 50),
    ("Call 3: + soql_query result",       query_tok, 200),
]
cumulative = 0
total_new_abc = 0
for name, added, output in steps_skip:
    cumulative += added
    call_total = cumulative + output
    total_new_abc += call_total
    print(f"  {name:40s}  {cumulative:>5,d} in + {output:>3d} out = {call_total:>5,d}")
print(f"  {'TOTAL':40s}  {total_new_abc:>20,d} tokens")
savings_abc = total_old - total_new_abc
print(f"  Saved: {savings_abc:,d} tokens ({savings_abc * 100 // total_old}% reduction)")

# ---- Summary ----
print(f"\n{'=' * 70}")
print(f"  SUMMARY")
print(f"{'=' * 70}")
print(f"  Before (4 calls, full describe):          {total_old:>6,d} tokens")
print(f"  After A+C (4 calls, slim describe):       {total_new_ac:>6,d} tokens  (-{savings_ac * 100 // total_old}%)")
print(f"  After A+B+C (3 calls, skip describe):     {total_new_abc:>6,d} tokens  (-{savings_abc * 100 // total_old}%)")
print(f"")
print(f"  GPT-4o @ 30K TPM:  Before={'EXCEEDED' if total_old > 30000 else 'OK':>10s}  A+C={'EXCEEDED' if total_new_ac > 30000 else 'OK':>10s}  A+B+C={'EXCEEDED' if total_new_abc > 30000 else 'OK':>10s}")
print(f"  GPT-4o @ 60K TPM:  Before={'TIGHT' if total_old > 30000 else 'OK':>10s}  A+C={'OK':>10s}  A+B+C={'OK':>10s}")
