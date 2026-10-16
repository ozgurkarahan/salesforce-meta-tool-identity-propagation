---
name: change-risk
description: >-
  Assess the business risk of planned ServiceNow change requests against Salesforce
  customers with deals closing soon. Use when the user asks about a planned change,
  maintenance window, upgrade or migration and which customers or deals it could affect.
---

# Change risk

Uses the `salesforce-crm`, `servicenow-itsm` and `customer360-correlation` skills.

## Steps
1. **Changes** — find the change(s) the user means, or upcoming ones:
   `query(table="change_request",
   query="short_descriptionLIKE<keyword>^ORdescriptionLIKE<keyword>",
   fields="number,short_description,description,type,risk,impact,state,start_date,end_date,company",
   limit=10)`. ServiceNow risk/impact: `1` High, `2` Medium/Moderate, `3` Low.
2. **Exposure** — identify affected customers from `company` and from systems named in
   the description (e.g. billing, payments, CRM, SSO).
3. **Deals** — open opportunities for those accounts closing within 60 days:
   `SELECT Account.Name, Name, Amount, StageName, CloseDate FROM Opportunity
   WHERE IsClosed = false AND Account.Name IN (...) AND CloseDate = NEXT_N_DAYS:60
   ORDER BY CloseDate LIMIT 50`.
4. **Service context** — open incidents for the same companies (a change landing on an
   already unstable customer is higher risk).

## Rating
- **High**: change risk/impact High, or a deal ≥ $250K closes within 14 days of the
  window, or a P1/P2 incident is open for that customer.
- **Medium**: deals close within 60 days of the window, or risk/impact Medium.
- **Low**: otherwise.

## Output
Per change: number, summary, window, risk/impact → affected accounts table (Account ·
Deal · Amount · Close date · Open incidents · Rating) → total pipeline exposed →
recommendations (notify account owners, adjust the window, confirm the rollback plan).
Do not modify the change request unless the user explicitly asks and confirms.
