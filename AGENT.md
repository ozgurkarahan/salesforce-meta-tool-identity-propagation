# AGENT.md

This file provides guidance to code agents when working with this repository.

## Project Overview

**Salesforce MCP OBO** — On-Behalf-Of (JWT Bearer) identity propagation for Salesforce MCP. User authenticates once to Azure AD; APIM exchanges the Azure AD token for a Salesforce token server-side via JWT Bearer flow. No Salesforce consent required. True OBO.

**Status:** OBO flow is **verified end-to-end** (2026-03-01). SF Login History confirms per-user identity propagation.

## Architecture

```
User → Chat App (MSAL.js) → AI Foundry Agent
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

### Key Paths

**Infrastructure:**
- `infra/main.bicep` — Orchestrator, all module wiring
- `infra/main.bicepparam` — Environment variable → Bicep param mapping
- `infra/modules/apim-sf-mcp-obo.bicep` — OBO APIM API, Named Values
- `infra/modules/apim-jwt-bearer-cert.bicep` — Key Vault → APIM certificate binding
- `infra/modules/sf-obo-connection.bicep` — Foundry UserEntraToken connection
- `infra/modules/cognitive.bicep` — AI Services account, project, App Insights connection
- `infra/modules/keyvault.bicep` — Key Vault + APIM RBAC access
- `infra/policies/sf-mcp-obo-policy.xml` — The OBO exchange policy (3-phase)
- `infra/policies/sf-mcp-obo-prm-policy.xml` — RFC 9728 PRM for OBO endpoint

**Application (unchanged between modes):**
- `src/salesforce-mcp/` — MCP server (6 tools, bearer passthrough)
- `src/chat-app/` — FastAPI + MSAL.js frontend

**Hooks & Scripts:**
- `hooks/postprovision.py` — Cert upload + Entra app + Foundry Agent + OBO connection setup
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
2. **sso** — Entra App Registration + SF Auth Provider (interactive browser login)
3. **demo** — Custom "Standard User - No Delete" profile + demo user + test data
4. **svcacct** — Service account with Minimum Access profile + `MCP_OBO_Service_Account` Permission Set
5. **fedid** — Set FederationIdentifier on SF users from Azure AD `oid`

> **Note:** These are SF Setup Steps (run offline before deployment). The Post-Deploy Steps (cert upload, Entra app, Foundry agent, OBO connection) are handled automatically by `hooks/postprovision.py`.

After setup, import PFX (private key + cert) into Azure Key Vault as `sf-jwt-bearer`.

### OBO Prerequisites (Azure side)
1. `certs/sf-jwt-bearer.pfx` exists locally (postprovision hook uploads to KV automatically)
2. APIM managed identity with "Key Vault Secrets User" RBAC role on KV (Bicep handles this)
3. `SF_JWT_BEARER_CERT_THUMBPRINT` auto-set by postprovision hook (or set manually)

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
