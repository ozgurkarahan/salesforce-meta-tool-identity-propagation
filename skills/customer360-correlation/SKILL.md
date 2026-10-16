---
name: customer360-correlation
description: >-
  How to build a unified customer view by joining Salesforce (CRM) and ServiceNow (ITSM)
  data. Use when a question names a customer/company, asks for a "360" or "full picture",
  or asks whether Salesforce cases and ServiceNow incidents are related.
---

# Customer 360 correlation

Uses the `salesforce-crm` and `servicenow-itsm` skills for query rules.

## Steps
1. **Tell the user the plan**: what you will query in each system.
2. **Salesforce** (one SOQL query where possible):
   `SELECT Id, Name, Industry, Type, AnnualRevenue,
     (SELECT Name, Amount, StageName, CloseDate FROM Opportunities WHERE IsClosed = false),
     (SELECT CaseNumber, Subject, Priority, Status FROM Cases WHERE IsClosed = false)
   FROM Account WHERE Name LIKE '%<company>%' LIMIT 5`
3. **ServiceNow** (one query per table, company names combined with `^OR`):
   open `incident`, open `problem`, upcoming `change_request` for the company.
4. **Correlate** using the keys below and present one unified view.

## Correlation keys
- **Company**: Salesforce `Account.Name` ↔ ServiceNow `company.name`. Treat legal-suffix
  variants as the same company ("Contoso" = "Contoso Ltd").
- **Issue**: a Salesforce Case and a ServiceNow Incident for the same company whose
  Subject/short_description share keywords (e.g. "API gateway", "SSO", "payment") and
  were opened within a few days of each other are likely the same issue seen from the
  customer side and the IT side.

## Presentation
- Sections: Account snapshot · Open pipeline (total + deals) · Open cases · Open
  incidents/problems · Upcoming changes · Correlations · Suggested next steps.
- For every correlation, state what matched (company, keywords, dates) and how
  confident you are. Never invent a link.
- Lead with monetary amounts when pipeline is involved.
- If one system has no data for the company, say so explicitly rather than omitting it.
