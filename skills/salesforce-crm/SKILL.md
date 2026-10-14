---
name: salesforce-crm
description: >-
  How to read and write Salesforce data with the salesforce_mcp tools (whoami,
  list_objects, describe_object, soql_query, search_records, write_record,
  process_approval). Use for any question about Salesforce accounts, contacts,
  opportunities, cases, approvals or custom objects, and before any Salesforce write.
---

# Salesforce CRM

## Workflow
1. **Identify the user only when needed.** If the user says "my"/"me", reuse a UserId
   already in this conversation; otherwise call `whoami` once. Filter with
   `OwnerId = '<UserId>'` or `CreatedById = '<UserId>'`.
2. **Find the object.** Skip for well-known standard objects (Account, Contact,
   Opportunity, Case, Lead, Task, Event, User). Otherwise call `list_objects(filter=...)`
   and use the `name` (API name), never the `label`.
3. **Learn the fields.**
   - Reads: skip `describe_object` when you only need common fields
     (`Id, Name, CreatedDate, OwnerId, LastModifiedDate`) or fields already seen in this
     conversation. Otherwise use `mode="slim"` (types, `referenceTo`, `childRelationships`)
     or `mode="names"` to only validate names.
   - Writes: `describe_object(mode="full")` is REQUIRED every time — picklists and
     validation rules change.
4. **Execute.**
   - `soql_query` for exact filters, joins and aggregates.
   - `search_records` (SOSL) when the object is unknown or for fuzzy keyword search.
   - `write_record(operation=create|update|upsert|delete)`.
   - `process_approval(action=Submit|Approve|Reject)`.
5. **Summarize** in plain language. Never dump raw JSON.

## SOQL rules
- Always include `LIMIT` (default 20–50) unless the user explicitly asks for all rows.
  `max_records` defaults to 100, cap 500.
- API names are PascalCase: `Account`, `OpportunityLineItem`, `Custom_Field__c`.
- Parent fields via dot notation: `SELECT Name, Account.Name FROM Opportunity`.
- Child subqueries use the `relationshipName` from `childRelationships`, not the object
  name: `SELECT Name, (SELECT Subject FROM Cases) FROM Account`.
- Open opportunities and open cases: `IsClosed = false`.
- Date literals: `CloseDate = NEXT_N_DAYS:90`, `CreatedDate = LAST_N_DAYS:30`.
- Quote string values with single quotes; escape embedded quotes.

## Error recovery
- `INVALID_FIELD` / `MALFORMED_QUERY`: the error contains `availableFields`
  (`name`, `type`, `referenceTo`). Fix the query from it and retry immediately — do NOT
  call `describe_object` again. If the field is absent, the org hides it; look for a path
  through the parent's `childRelationships`.
- `INSUFFICIENT_ACCESS`: the user lacks permission. Say which object/field and stop.
- `ENTITY_IS_DELETED`: tell the user the record was deleted.
- `UNABLE_TO_LOCK_ROW`: retry once.
- Explain any error in plain language before retrying.

## Writes and approvals — mandatory confirmation
Before ANY `write_record` or `process_approval` call, show the user the object, record
(Id and Name), operation and exact field values, then wait for an explicit "yes".
Never write on an implied or ambiguous request. Never delete or reject without a
separate explicit confirmation.
