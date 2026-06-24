# AGENT.md

This file provides guidance to code agents when working with this repository.

## Project Overview

**Salesforce MCP OBO** — On-Behalf-Of (JWT Bearer) identity propagation for Salesforce MCP. User authenticates once to Azure AD; APIM exchanges the Azure AD token for a Salesforce token server-side via JWT Bearer flow. No Salesforce consent required. True OBO.

**Status:** OBO flow is **verified end-to-end** (2026-03-01). SF Login History confirms per-user identity propagation.

## Architecture

### Multi-Agent Headless Chat App

```
User → Chat App (MSAL.js) → Sign in
  → GET /api/agents (auth required)
    → Azure Resource Graph (managed identity) → discover all Foundry projects
    → AIProjectClient.agents.list() per project → discover all agents
  → Agent selector UI (project → agent cards)
  → POST /api/chat { agent_name, project_endpoint, message }
  → AI Foundry Agent (any project, any agent)
```

The chat app is **agent-agnostic** — it works with any AI Foundry agent across any project in the subscription. Agent discovery is fully dynamic:
1. **Resource Graph** finds all `Microsoft.CognitiveServices/accounts/projects`
2. **Foundry SDK** lists agents in each project
3. **Frontend** shows project → agent selector with business-oriented prompts
4. **5-min cache** avoids repeated API calls

### Salesforce OBO Flow (per-agent)

```
AI Foundry Agent (salesforce-assistant)
  → Foundry acquires Azure AD token (UserEntraToken connection)
  → APIM validates Azure AD JWT
  → APIM Phase 1: service token → SOQL lookup (oid → SF username)
  → APIM Phase 2: JWT Bearer exchange (SF username → SF access token)
  → APIM Phase 3: forwards SF token to MCP Server
  → Salesforce MCP Server (FastMCP) → Salesforce APIs
```

## OBO Flow — How It Works

### Three-Phase Token Exchange (APIM Policy)

1. **Phase 0 — Validate Azure AD token:** `validate-jwt` checks the user's Entra token (both v1 and v2 issuers accepted). Extracts user identity via `{{IdentityClaimName}}` claim (default: `oid`).

2. **Phase 1 — Resolve SF username:** Checks cache for `sf-username-{oid}`. On miss: obtains a service token via JWT Bearer for `{{SfServiceAccountUsername}}`, then runs a SOQL query (`SELECT Username FROM User WHERE FederationIdentifier = '{oid}'`). Caches mapping for 1 hour.

3. **Phase 2 — Get SF user token:** Checks cache for `sf-token-{username}`. On miss: creates JWT Bearer assertion with `sub = SF username`, signs with Key Vault certificate, exchanges at SF token endpoint. Caches for 30 minutes.

4. **Phase 3 — Forward:** Replaces `Authorization` header with SF access token, forwards to MCP backend.

### Caching Performance
- Service token: cached 30 min (amortized across all users)
- Username mapping: cached 1 hour per user
- User token: cached 30 min per user
- **Warm user overhead: ~0ms** (all three cache hits)

### Error Recovery
- SF backend 401 → evicts user token from cache → next request re-exchanges automatically
- Service token failure on SOQL lookup → evicts service token → next request re-acquires
- User not mapped → returns 403 with `user_not_mapped` error

### UserEntraToken Connection (Foundry)

The `salesforce-obo` connection stores **no credentials**. It's a configuration that tells Foundry how to acquire the user's token:
- `authType: UserEntraToken` — acquire user's Entra token automatically
- `audience: https://ai.azure.com` — request token for this audience (must match APIM `validate-jwt`)
- `target: https://apim-.../salesforce-mcp-obo/mcp` — send requests here

## Customer 360 Agent

The **customer360-assistant** connects to both Salesforce (CRM) and ServiceNow (ITSM) via MCP tools, enabling unified cross-system queries.

### Capabilities

- **Unified lookup**: Full customer profile from both systems by company name
- **Cross-system correlation**: Match SF Cases with SN Incidents by company, keywords, timing
- **Revenue at risk**: Cross-reference P1/P2 incidents with open opportunities and pipeline value
- **Meeting prep**: Combined account summary, open deals, and active incidents
- **Change risk**: Assess SN Change Requests against SF accounts with upcoming renewals

### Architecture

```
Customer 360 Agent (AI Foundry)
  ├── salesforce_mcp (7 tools) → salesforce-obo connection → APIM → SF MCP Server
  ├── servicenow_mcp (3 tools) → servicenow-obo connection → APIM → SN MCP Server
  └── MemorySearchTool (project-memory store, per-user scope)
```

Both MCP tools use OBO identity propagation — the user's Azure AD token flows through to each backend system via APIM JWT Bearer exchange.

### Token Optimization

System instructions are trimmed to ~437 tokens (44% reduction from initial 782). Key design choices:
- Explicit "always fetch fresh data" rule prevents memory-only answers
- Removed Tool Routing section (tool names are self-descriptive)
- Trimmed Correlation Patterns to 2 proven patterns (company name + keywords)
- SF `soql_query` default `max_records` reduced from 2000 to 100 (cap 500)
- E2E test includes 429 retry with 30s backoff and 15s inter-turn pacing
- Typical 6-turn session: ~124K tokens (25% below baseline)

### Prerequisites

1. This project deployed (`azd up`) — provides SF MCP server, APIM, Foundry project
2. `snow-meta-tool` project deployed (`azd up`) — provides SN MCP server and `servicenow-obo` connection
3. Both Container Apps running (SF + SN MCP servers)

### Demo Data

Correlated data across both systems is required for cross-system scenarios:

```bash
python scripts/seed_demo_data.py \
  --sf-org <alias> \
  --sn-instance https://devXXXXX.service-now.com \
  --sn-admin-password <pw>
```

The script pre-creates companies in SN's `core_company` table before seeding incidents (required because `company` is a reference field).

See `docs/DEMO_DATA_SETUP.md` for details, `docs/TEST_PROMPTS_360.md` for test scenarios.

### E2E Test

```bash
python scripts/test_e2e_customer360.py
```

Runs 6 progressive scenarios, validates both MCP tools are called per turn, reports token usage. Handles 429 rate limits with retry backoff. See `.ai/e2e-results-history.md` for optimization progression.

---

## Development Quick Reference

### Deploy
```bash
azd env new obo
azd env set SF_INSTANCE_URL "https://your-org.my.salesforce.com"
azd env set SF_CONNECTED_APP_CLIENT_ID "<connected-app-consumer-key>"
azd env set SF_SERVICE_ACCOUNT_USERNAME "<svc@your-org.my.salesforce.com>"
azd up
# Postprovision hook uploads certs/sf-jwt-bearer.pfx to KV and sets SF_JWT_BEARER_CERT_THUMBPRINT
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SF_INSTANCE_URL` | Yes | SF org My Domain URL |
| `SF_CONNECTED_APP_CLIENT_ID` | Yes | Connected App consumer key |
| `SF_SERVICE_ACCOUNT_USERNAME` | Yes | SF service account username for SOQL lookups |
| `SF_JWT_BEARER_CERT_THUMBPRINT` | Auto | Auto-set by postprovision hook from KV cert |
| `SF_JWT_BEARER_CERT_NAME` | No | Key Vault certificate name (default: `sf-jwt-bearer`) |
| `IDENTITY_CLAIM_NAME` | No | JWT claim for user identity (default: `oid`) |
| `AGENT_BOT_MSA_APP_ID` | Auto | Foundry-managed identity clientId (auto-set by postprovision Step 5) |
| `AGENT_BOT_NAME` | Auto | Bot Service resource name (auto-set by postprovision Step 7) |
| `TEAMS_APP_DEVELOPER_NAME` | For Teams | Developer name in Teams manifest |
| `TEAMS_APP_PRIVACY_URL` | For Teams | Privacy URL in Teams manifest |
| `TEAMS_APP_TERMS_URL` | For Teams | Terms of use URL in Teams manifest |
| `FOUNDRY_PROJECTS` | No | JSON list of Foundry projects `[{name, endpoint}]` — overrides dynamic discovery |
| `AGENTS_CONFIG` | No | Static JSON agent config — fallback when dynamic discovery unavailable |

### Key Paths

**Infrastructure:**
- `infra/main.bicep` — Orchestrator, all module wiring
- `infra/main.bicepparam` — Environment variable → Bicep param mapping
- `infra/modules/apim-sf-mcp-obo.bicep` — OBO APIM API (native MCP type), backend, Named Values
- `infra/modules/apim-jwt-bearer-cert.bicep` — Key Vault → APIM certificate binding
- `infra/modules/sf-obo-connection.bicep` — Foundry UserEntraToken connection
- `infra/modules/cognitive.bicep` — AI Services account, project, App Insights connection
- `infra/modules/subscription-role-assignment.bicep` — Subscription-level RBAC (Reader for Resource Graph discovery)
- `infra/modules/bot-service.bicep` — Bot Service + Teams/DirectLine channels (conditional on msaAppId)
- `infra/modules/keyvault.bicep` — Key Vault + APIM RBAC access
- `infra/policies/sf-mcp-obo-policy.xml` — The OBO exchange policy (3-phase)
- `infra/policies/sf-mcp-obo-prm-policy.xml` — RFC 9728 PRM for OBO endpoint

**Application:**
- `src/salesforce-mcp/` — MCP server (7 tools, bearer passthrough)
- `src/chat-app/` — FastAPI backend + vanilla JS SPA with:
  - **Multi-agent headless architecture** — works with any AI Foundry agent/project
  - Dynamic agent discovery via Azure Resource Graph + `AIProjectClient.agents.list()`
  - Agent selector UI (project → agent cards with business-oriented prompts)
  - MSAL.js auth (redirect fallback, not popup — COOP compat)
  - Tool panel sidebar (waterfall timeline, stats, export)
  - Debug panel (App Insights log tail + instant local logs)
  - Markdown rendering (marked.js, bundled locally)
  - Memory search visibility (Foundry MemorySearchTool results)
  - Teams Bot Framework endpoint (`POST /api/messages`)
- `src/shared/foundry_helpers.py` — shared agent call helpers (multi-agent: accepts `agent_name` + `project_endpoint`)

**Hooks & Scripts:**
- `hooks/postprovision.py` — Steps 0-8: cert upload, Entra app, Foundry agent, OBO connection, Agent Application, Agent Deployment, Bot Service bootstrap, Teams org catalog
- `assets/teams/` — Teams app icons (color.png 192x192, outline.png 32x32)
- `scripts/sf_utils.py` — Shared SF/CLI primitives (run, SOQL, metadata deploy, REST helpers)
- `scripts/setup-sf-org.py` — Complete 5-step SF org setup orchestrator (Connected App, SSO, Demo User, Service Account, Federation IDs)
- `scripts/test-salesforce-mcp.py` — E2E MCP server test

### OBO Prerequisites (Salesforce side)

All SF setup is handled by `scripts/setup-sf-org.py`:

```bash
python scripts/setup-sf-org.py --org <alias> --email <email> --cert certs/sf-jwt-bearer.crt
```

The 5 SF Setup Steps (run individually with `--only <step>`):
1. **eca** — Create Connected App with JWT Bearer flow + X.509 certificate + profile pre-authorization
2. **sso** — Entra Enterprise App (SAML) + SF SamlSsoConfig + self-signed cert (no Apex)
3. **demo** — Custom "Standard User - No Delete" profile + demo user + test data
4. **svcacct** — Service account with Minimum Access profile + `MCP_OBO_Service_Account` Permission Set
5. **fedid** — Set FederationIdentifier on SF users from Azure AD `oid`

> **Note:** These are SF Setup Steps (run offline before deployment). The Post-Deploy Steps (cert upload, Entra app, Foundry agent, OBO connection) are handled automatically by `hooks/postprovision.py`.

After setup, import PFX (private key + cert) into Azure Key Vault as `sf-jwt-bearer`.

### OBO Prerequisites (Azure side)
1. `certs/sf-jwt-bearer.pfx` exists locally (postprovision hook uploads to KV automatically)
2. APIM managed identity with "Key Vault Secrets User" RBAC role on KV (Bicep handles this)
3. `SF_JWT_BEARER_CERT_THUMBPRINT` auto-set by postprovision hook (or set manually)

### Teams Publishing Prerequisites
1. `AppCatalog.ReadWrite.All` Graph API permission on deployer identity (with admin consent)
2. Set: `TEAMS_APP_DEVELOPER_NAME`, `TEAMS_APP_PRIVACY_URL`, `TEAMS_APP_TERMS_URL`
3. First `azd up` creates Agent Application + bootstraps Bot Service; second `azd up` lets Bicep manage the Bot Service

### IdP Flexibility

The `IdentityClaimName` Named Value (default: `oid`) controls which JWT claim is used for user identity. To switch from Azure AD to another IdP:

| What changes | Where | Notes |
|---|---|---|
| OIDC discovery URL | `sf-mcp-obo-policy.xml` line 16 | PingFed/Okta OIDC endpoint |
| Issuer validation | `sf-mcp-obo-policy.xml` lines 21-24 | New issuer(s) |
| Identity claim name | `IDENTITY_CLAIM_NAME` env var | `oid` → `sub` or custom |
| Audience | `sf-mcp-obo-policy.xml` line 18 | Match IdP config |
| Foundry connection type | `sf-obo-connection.bicep` | `UserEntraToken` is Azure-only; other IdPs need `CustomKeys` |

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| 401 "Invalid Azure AD token" | Token issuer/audience mismatch | Check `validate-jwt` issuers include both v1 and v2 |
| 502 "SF Service Token Failed" | Bad cert, wrong client ID, or service account not pre-authorized | Verify cert thumbprint, client ID, and `MCP_OBO_Service_Account` Permission Set assignment |
| 403 "User Not Mapped" | No SF user with matching FederationIdentifier | Run `setup-sf-org.py --only fedid` |
| 502 "SF Token Exchange Failed" | Target SF user not pre-authorized for the Connected App | Assign user's profile to the Connected App via SetupEntityAccess |
| 500 (KeyNotFoundException) | Certificate thumbprint wrong or missing Named Value | Verify `SF_JWT_BEARER_CERT_THUMBPRINT` matches actual cert |
| "Missing required query parameter: audience" | `audience` missing on Foundry connection | Add `audience: 'https://ai.azure.com'` to connection properties |

### SF Org Setup (after new Dev Trial)
```bash
# Full 5-step setup
python scripts/setup-sf-org.py --org <alias> --email <admin-email> --cert certs/sf-jwt-bearer.crt

# Run specific steps
python scripts/setup-sf-org.py --org <alias> --email <email> --only eca demo
python scripts/setup-sf-org.py --org <alias> --email <email> --skip sso fedid

# Federation IDs (dry run)
python scripts/setup-sf-org.py --org <alias> --email <email> --only fedid --dry-run

# Cleanup (deactivate demo/svc users, delete test data)
python scripts/setup-sf-org.py --org <alias> --email <email> --cleanup
```

---

## Workflow Rules

Read `~/projects/memory/agent-config/workflow.md` for the full set of global workflow rules. Key rules summarized here:

1. **Plan Before Coding** — For any task with 3+ steps, outline the approach first. Get approval before implementing.
2. **Verify Before Done** — Never mark a task complete without proving it works.
3. **Learn From Mistakes** — After any correction, record the lesson in the project wiki page `~/projects/memory/wiki/projects/salesforce-meta-tool-id-prop.md` (and append `~/projects/memory/log.md`). Review the wiki at session start. The legacy `.ai/lessons-learned.md` was REMOVED — lessons live only in the wiki project page `~/projects/memory/wiki/projects/salesforce-meta-tool-id-prop.md`.
4. **No Blind Retries** — Diagnose root cause on failure. Don't retry non-transient errors.
5. **Keep It Simple** — Don't add features, refactor code, or make improvements beyond what was asked.

## Platform & Environment

Read `~/projects/memory/agent-config/platform.md` for full platform preferences and domain knowledge index.

- Windows 11 + Git Bash (MSYS)
- Python 3.11+ (`python` not `python3`)
- Always `encoding="utf-8", errors="replace"` for subprocess on Windows

## Reference Documents

| Document | Contents |
|----------|----------|
| `~/projects/memory/wiki/projects/salesforce-meta-tool-id-prop.md` | Project-specific lessons & debugging history (single source of truth; `.ai/lessons-learned.md` was REMOVED) |
| `.ai/project-reference.md` | Technical details, implementation caveats |
| `~/projects/memory/agent-config/knowledge/*.md` | Cross-project domain knowledge |

## What NOT To Do

- Do not create files unless absolutely necessary — prefer editing existing files.
- Do not add comments, docstrings, or type annotations to code you didn't change.
- Do not over-engineer simple solutions.
- Do not commit secrets, credentials, or `.env` files.
- Do not skip tests or verification steps.
