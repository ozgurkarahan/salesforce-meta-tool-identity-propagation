# Teams Integration Guide

Expose the Salesforce MCP agent through Microsoft Teams and M365 Copilot
using Azure AI Foundry's native publishing feature.

## Overview

There are two ways to reach Teams. Neither needs custom bot code in the data path:

- **Automated (`azd up`):** postprovision Steps 7–9 publish **both**
  `salesforce-assistant` and `customer360-assistant` (v3). Each one gets a Foundry
  Agent Application, an immutable `<agent>-v<N>` deployment that receives 100% of
  the traffic only after a smoke test passes, and its own Azure Bot Service, whose
  messaging endpoint is the application's activity-protocol endpoint. Each one
  also gets its own Teams app (1:1, group chat and team scopes) in your org
  catalog. Every publish patch-bumps the manifest version from the latest
  catalog `appDefinition`. Publishing requires the `TEAMS_APP_*` variables and
  `AppCatalog.ReadWrite.All` on the deployer's Graph token (see AGENT.md, *Teams
  Publishing Prerequisites*). If the scope is missing, the Teams step logs a
  warning and the rest of the hook still succeeds. Roll back the agents with
  `python hooks/postprovision.py --rollback`.
- **Foundry portal:** publish any agent to Teams **and Microsoft 365 Copilot**
  from the Azure AI Foundry portal (steps below). This path also makes the agent
  available in Microsoft 365 Copilot.

Foundry handles the channel integration, SSO, and tool approval UX natively.

**Validated:** OBO identity propagation works end-to-end through the
Foundry-published Teams/Copilot channel. Per-user Salesforce identity is
preserved (confirmed by SF permission enforcement on delete operations).

## Architecture

```
                        +-- Web Chat App (custom)
                        |    MSAL.js -> UserTokenCredential -> Foundry SDK
                        |
Foundry Agent <---------+
  (salesforce-assistant) |
                        +-- Teams / M365 Copilot (Foundry Native Publishing)
                             Foundry handles SSO + tool approval UX
                             OBO identity propagation preserved
```

## Setup Steps

### Prerequisites

- Azure AI Foundry project with the `salesforce-assistant` agent deployed
- `Microsoft.BotService` resource provider registered in the Azure subscription
- Teams admin consent for custom apps (or M365 Copilot access)

### Register BotService Provider (one-time)

```bash
az provider register --namespace Microsoft.BotService
# Wait ~4 minutes for propagation
az provider show --namespace Microsoft.BotService --query registrationState -o tsv
```

### Publish from Foundry

1. Open the **Azure AI Foundry portal** at your project endpoint
2. Navigate to **Agents** and find `salesforce-assistant`
3. Click **Publish** -> select **Microsoft Teams** (and/or M365 Copilot)
4. Foundry creates a dedicated Entra identity for the published agent
5. Follow the prompts to complete publishing

**Important:** Publish from the AI Foundry portal, NOT from the M365 Admin
Center. The admin center is for approving/distributing already-published
agents, not for initial publishing.

### Verify OBO Identity Propagation

1. Send a message in Teams/Copilot: "show my accounts"
2. Verify Salesforce data returns with correct user identity
3. Check SF Login History for per-user `JWT Bearer Token Exchange` entries
4. Test permission enforcement: try a write operation the user shouldn't have access to

## How It Works

### Auth Flow

```
1. User sends message in Teams/Copilot
2. Foundry acquires the user's delegated token via the salesforce-obo-oauth2
   connection (OAuth2 identity passthrough, aud=api://<MCP OAuth app>;
   one-time consent per user)
3. Foundry sends that token to APIM
4. APIM validates token, performs 3-phase OBO exchange:
   a. Service account JWT Bearer -> SF service token
   b. SOQL lookup: FederationIdentifier (oid) -> SF Username
   c. User-specific JWT Bearer -> SF per-user token
5. MCP server executes tool call against Salesforce with user's identity
6. Response flows back through Foundry to Teams/Copilot
```

### Tool Approval

Both agents use `require_approval: "never"`: SDK-level approval prompts break in
Teams. Writes are guarded conversationally instead. The lean system prompt and the
`salesforce-crm` / `servicenow-itsm` skills require the agent to confirm with the
user before any create, update, delete or approval action. The v3 E2E script
checks this in its *Write guard* turn.

### Identity Caveat

When published via Foundry, the agent gets its own dedicated Entra identity.
This changes the `appid` claim in tokens but does NOT break OBO because:

- The APIM policy validates the audience `api://<MCP_OAUTH_CLIENT_ID>` (v2 OAuth2
  identity passthrough; `https://ai.azure.com` is still accepted for backward
  compatibility only), not `appid`
- The user's `oid` is preserved in the token (identity propagation intact)
- The 3-phase OBO exchange uses `oid` to look up the SF username

## v3 Teams smoke test (manual)

The hook publishes both agents, but a human runs the Teams test. Use two
accounts: the admin and the demo user. Record the date, the agent versions
(`SF_AGENT_VERSION`, `C360_AGENT_VERSION` in `azd env get-values`), and the
results in `.ai/e2e-results-history.md`.

**Before you start**

- [ ] `azd env get-values` shows `SF_DEPLOYMENT_ID` / `C360_DEPLOYMENT_ID`. The hook
      already asserted that each application routes 100% to them.
- [ ] The post-route E2E passed:
      `python scripts/test_e2e_customer360.py --path app`.
- [ ] Both apps appear in Teams → Apps → *Built for your org*: **Salesforce
      Assistant** and **Customer 360 Assistant**. If they don't, the catalog step
      warned about `AppCatalog.ReadWrite.All`. Upload them from the Teams admin
      center instead.

**For each user, in a 1:1 chat with each app**

| # | Message | Expected |
|---|---------|----------|
| 1 | `Who am I in Salesforce?` (both apps) | First use shows a sign-in/consent card. After consent, the answer gives **this user's** SF username (not the other account's) |
| 2 | `Who am I in ServiceNow?` (Customer 360) | A second consent for ServiceNow, then this user's `sys_user` name |
| 3 | `Give me a Customer 360 view for Contoso Ltd` | Data from both systems in one answer |
| 4 | `P1/P2 incidents with affected Salesforce accounts — rank by revenue at risk` | Ranked list that leads with amounts (`revenue-at-risk` skill) |
| 5 | `Change the Industry of Acme Corp to Retail` | The agent asks for confirmation and **does not write** until the user says yes |
| 6 | Same as 1, in a group chat with both users | Each user sees their own identity, with no cross-user leakage |

**Verify identity outside Teams (run these after the test)**

- [ ] Salesforce → Setup → Login History: one `JWT Bearer` login per test user
      in the test window, under each user's own username.
- [ ] APIM gateway logs (workspace `log-sf-mcp-obo`, legacy table) contain 200s
      for both APIs, with no 401/403 in the window:

      ```kql
      AzureDiagnostics
      | where Category == 'GatewayLogs' and TimeGenerated > ago(2h)
      | where apiId_s in ('salesforce-mcp-obo','servicenow-mcp-obo')
      | summarize calls=count(), errors=countif(responseCode_d >= 400)
          by apiId_s, bin(TimeGenerated, 10m)
      ```

- [ ] ServiceNow → System Logs → Transactions: the queries ran as each
      user's `sys_user`, not as an integration account.

If anything fails, roll back with `python hooks/postprovision.py --rollback`. It
routes each application back to the deployment recorded before the publish.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Publish failed" in M365 Admin Center | Wrong publishing location | Publish from AI Foundry portal, not M365 Admin Center |
| `Microsoft.BotService` not registered | Resource provider not registered | `az provider register --namespace Microsoft.BotService` |
| 401/403 at APIM | Token audience mismatch | Check the `aud` claim: it should be `api://<MCP_OAUTH_CLIENT_ID>` (v2). The `X-Auth-Error-Reason` header gives the exact cause |
| No Salesforce data | OBO flow broken | Check APIM logs, verify SF Login History |
| Tool calls denied by SF | Correct behavior | OBO identity propagation working; user lacks SF permissions |

## Alternative: Custom Bot (Phase B)

If Foundry Native Publishing doesn't meet your needs (e.g., custom UX,
Adaptive Cards for rich data display, or identity issues), a custom bot
can be built using the M365 Agents SDK. The shared Foundry helpers in
`src/shared/foundry_helpers.py` support multi-channel adoption:

- Each channel acquires the user's Azure AD token independently
- Calls `call_agent()` and `approve_tools()` from shared helpers
- No dependency on the web chat app backend

This approach requires: Azure Bot Service, bot Entra registration, Teams
app manifest, and a container app for the bot. See git history for the
full Phase B implementation (removed after Phase A validation succeeded).
