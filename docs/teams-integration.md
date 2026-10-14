# Teams Integration Guide

Expose the Salesforce MCP agent through Microsoft Teams and M365 Copilot
using Azure AI Foundry's native publishing feature.

## Overview

There are two ways to reach Teams. Neither needs custom bot code in the data path:

- **Automated (`azd up`):** postprovision Steps 5–8 publish `salesforce-assistant`
  as a Foundry Agent Application. They bootstrap an Azure Bot Service whose
  messaging endpoint is Foundry's activity-protocol endpoint, then upload a Teams
  app (1:1, group chat and team scopes) to your org catalog. This requires the
  `TEAMS_APP_*` variables and `AppCatalog.ReadWrite.All` (see AGENT.md, *Teams
  Publishing Prerequisites*).
- **Foundry portal:** publish any agent, for example `customer360-assistant`, to
  Teams **and Microsoft 365 Copilot** from the Azure AI Foundry portal (steps
  below). This is the path that makes the agent available in Microsoft 365
  Copilot.

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

Foundry's native Teams/Copilot integration shows tool approval prompts
with Approve/Deny buttons. The user decides which MCP tools to execute.
This is configured via `require_approval: "always"` on the agent's MCP tool.

### Identity Caveat

When published via Foundry, the agent gets its own dedicated Entra identity.
This changes the `appid` claim in tokens but does NOT break OBO because:

- The APIM policy validates the audience `api://<MCP_OAUTH_CLIENT_ID>` (v2 OAuth2
  identity passthrough; `https://ai.azure.com` is still accepted for backward
  compatibility only), not `appid`
- The user's `oid` is preserved in the token (identity propagation intact)
- The 3-phase OBO exchange uses `oid` to look up the SF username

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
