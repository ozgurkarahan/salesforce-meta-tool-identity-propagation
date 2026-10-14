---
name: servicenow-itsm
description: >-
  How to read and write ServiceNow ITSM data with the servicenow_mcp tools (discover,
  query, write). Use for any question about incidents, problems, change requests,
  requested items, approvals or companies in ServiceNow, and before any ServiceNow write.
---

# ServiceNow ITSM

## Workflow
1. **Pick the table.** Common tables need no lookup: `incident`, `problem`,
   `change_request`, `sc_req_item`, `sysapproval_approver`, `core_company`. Otherwise call
   `discover(filter=...)` once.
2. **Learn the fields.**
   - Reads: skip `discover(table=...)` when the standard fields below are enough.
     Use `mode="names"` to validate names only; `mode="compact"` (default) for types and
     references.
   - Writes: `discover(table=..., include_choices=True)` is REQUIRED first.
   - If `discover` returns 403, fall back to the standard fields.
3. **Query** with `query(table, query, fields, limit)`.
4. **Summarize** in plain language. Never dump raw JSON.

Standard fields: `number, short_description, description, priority, state, urgency,
impact, category, assignment_group, assigned_to, company, opened_at, sys_created_on`.
Change requests add: `type, risk, start_date, end_date`.

## Query rules
- ALWAYS pass `fields=` with only the columns you need.
- Encoded query syntax: `field=value`, `fieldLIKEvalue`, `fieldSTARTSWITHvalue`,
  `field!=value`, `field>value`, `fieldINa,b`, `fieldISEMPTY`; AND `^`, OR `^OR`,
  sort `^ORDERBYfield` / `^ORDERBYDESCfield`.
- Priority: `1` Critical (P1), `2` High (P2), `3` Moderate, `4` Low.
  Incident state: `1` New, `2` In Progress, `3` On Hold, `6` Resolved, `7` Closed.
  Open incidents: `state<6` (or `active=true`).
- Company lookups: `company` references `core_company`. Use ONE query that covers exact
  and variant names: `company.name=Contoso Ltd^ORcompany.nameLIKEContoso`.
  Do not issue separate exact and LIKE queries.
- Several companies: combine in one query with `^OR`:
  `company.name=Acme Corp^ORcompany.name=Contoso Ltd`.
- Keyword search: `query(table, text_search="api gateway", fields=...)`.
- Counts/breakdowns: `aggregate=True, group_by="priority"` instead of fetching rows.

## Writes and approvals — mandatory confirmation
- Updates/deletes need the 32-char `sys_id` (query it first).
- Approvals: update `sysapproval_approver` with `state=approved|rejected`.
- Before ANY `write` call, show the table, record (number + sys_id), operation and exact
  field values, then wait for an explicit "yes". Never delete without a separate
  explicit confirmation.
