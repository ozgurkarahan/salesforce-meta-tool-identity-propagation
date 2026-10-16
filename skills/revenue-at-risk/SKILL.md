---
name: revenue-at-risk
description: >-
  Quantify sales pipeline exposed to service issues by crossing high-priority ServiceNow
  incidents with open Salesforce opportunities. Use for "revenue at risk", "which deals are
  affected by incidents", P1/P2 impact, executive cross-system reports, or ranking
  accounts by risk.
---

# Revenue at risk

Uses the `salesforce-crm`, `servicenow-itsm` and `customer360-correlation` skills.

## Steps
1. **Incidents** — one ServiceNow query:
   `query(table="incident", query="priority<=2^state<6^ORDERBYpriority",
   fields="number,short_description,priority,state,company,opened_at", limit=50)`.
   If an incident has no company, infer it from `short_description`/`description` only
   when the name is explicit, and say you inferred it.
2. **Affected companies** — distinct company names from step 1.
3. **Pipeline** — one SOQL query for all of them:
   `SELECT Account.Name, Name, Amount, StageName, CloseDate FROM Opportunity
   WHERE IsClosed = false AND Account.Name IN ('A','B',...) ORDER BY Amount DESC LIMIT 100`
4. **Compute** per account: open pipeline total, number of P1 and P2 incidents, nearest
   close date.
5. **Rank** accounts by risk: P1 before P2, then pipeline amount, then nearest close date.

## Output
- Start with the headline: **total pipeline at risk** and the number of accounts.
- Table: Account · Pipeline at risk · P1 · P2 · Nearest close date · Key incident(s).
- Then 2–3 recommended actions (e.g. escalate the incident tied to the largest deal
  closing soonest).
- Format amounts as currency (e.g. $1.2M, $450K). Only show totals computed from
  records, never untraceable estimates.
- If no high-priority incidents are open, say so and stop.
