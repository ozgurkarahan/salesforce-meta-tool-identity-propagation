---
name: meeting-prep
description: >-
  Produce a concise customer meeting brief (QBR, account call, escalation call) from
  Salesforce and ServiceNow. Use when the user asks to prepare for a meeting or call with
  a customer, or wants talking points for an account.
---

# Meeting prep

Uses the `salesforce-crm`, `servicenow-itsm` and `customer360-correlation` skills.

## Steps
1. Gather the unified view (customer360-correlation steps 2–3), plus key contacts:
   `SELECT Name, Title, Email FROM Contact WHERE Account.Name LIKE '%<company>%' LIMIT 5`.
2. Add recent history when useful: cases and incidents from the last 30 days
   (`CreatedDate = LAST_N_DAYS:30` / `sys_created_on>javascript:gs.daysAgoStart(30)`).

## Brief format (keep it to one screen)
1. **Headline** — one sentence: relationship status and the single most important topic.
2. **Account** — industry, type, annual revenue, owner.
3. **Who you'll meet** — key contacts with titles.
4. **Commercial** — open pipeline total, top deals (amount, stage, close date).
5. **Service health** — open cases and incidents by priority; any correlated issues.
6. **Upcoming changes** that could affect the customer.
7. **Talking points** — 3–5 bullets: acknowledge open issues first, then commercial topics.
8. **Risks / asks** — what could derail the deal and what to ask for.

Lead with monetary amounts. Mark anything inferred as inferred.
