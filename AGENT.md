# AGENT.md

This file provides guidance to code agents when working with this repository.

## Project Overview

**Salesforce MCP OBO** — fork of `meta-tool-salesforce` with **On-Behalf-Of (JWT Bearer) identity propagation**. User authenticates once to Azure AD; APIM exchanges the Azure AD token for a Salesforce token server-side via JWT Bearer flow. No Salesforce consent required. No dual auth. True OBO.

**Status:** OBO flow is **verified end-to-end** (2026-03-01). SF Login History confirms per-user identity propagation.

## Architecture

### OBO Mode (primary)
```
User → Chat App (MSAL.js) → AI Foundry Agent
  → Foundry acquires Azure AD token (UserEntraToken connection)
  → APIM validates Azure AD JWT
  → APIM Phase 1: service token → SOQL lookup (oid → SF username)
  → APIM Phase 2: JWT Bearer exchange (SF username → SF access token)
  → APIM Phase 3: forwards SF token to MCP Server
  → Salesforce MCP Server (FastMCP) → Salesforce APIs
```

### OAuth2 Mode (preserved, original)
```
User → Chat App → AI Foundry Agent → ApiHub (SF PKCE consent)
  → APIM (validates SF JWT) → MCP Server → Salesforce APIs
```

## Auth Modes

| Mode | `SF_AUTH_MODE` | User Auth | Token Exchange | Status |
|------|---------------|-----------|----------------|--------|
| OAuth2/PKCE | `oauth2` (default) | Azure AD + SF consent | ApiHub manages SF tokens | Working (inherited) |
| OBO/JWT Bearer | `obo` | Azure AD only | APIM exchanges via JWT Bearer | **Verified** |

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

### Deploy (OBO mode)
```bash
azd env new obo
azd env set SF_AUTH_MODE obo
azd env set SF_INSTANCE_URL "https://your-org.my.salesforce.com"
azd env set SF_CONNECTED_APP_CLIENT_ID "<connected-app-consumer-key>"
azd env set SF_JWT_BEARER_CERT_THUMBPRINT "<certificate-thumbprint>"
azd env set SF_SERVICE_ACCOUNT_USERNAME "<admin@your-org.my.salesforce.com>"
azd up
```

### Deploy (OAuth2 mode — existing behavior)
```bash
azd env set SF_AUTH_MODE oauth2   # or omit (default)
azd env set SF_INSTANCE_URL "https://your-org.my.salesforce.com"
azd env set SF_CONNECTED_APP_CLIENT_ID "<consumer-key>"
azd env set SF_CONNECTED_APP_CLIENT_SECRET "<consumer-secret>"
azd up
```

### Environment Variables

| Variable | OAuth2 | OBO | Description |
|----------|--------|-----|-------------|
| `SF_AUTH_MODE` | `oauth2` (default) | `obo` | Auth flow selection |
| `SF_INSTANCE_URL` | Required | Required | SF org My Domain URL |
| `SF_CONNECTED_APP_CLIENT_ID` | Required | Required | Connected App consumer key |
| `SF_CONNECTED_APP_CLIENT_SECRET` | Required | Not needed | Connected App consumer secret |
| `SF_JWT_BEARER_CERT_THUMBPRINT` | — | Required | Certificate thumbprint for APIM policy |
| `SF_JWT_BEARER_CERT_NAME` | — | `sf-jwt-bearer` | Key Vault certificate name |
| `SF_SERVICE_ACCOUNT_USERNAME` | — | Required | SF admin username for SOQL lookups |
| `IDENTITY_CLAIM_NAME` | — | `oid` (default) | JWT claim for user identity (change for non-Azure IdPs) |

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
- `hooks/postprovision.py` — Entra app + Foundry Agent + connection setup (auth-mode-aware)
- `scripts/setup-sf-obo-eca.py` — Create SF Connected App for JWT Bearer via Metadata API
- `scripts/set-sf-federation-id.py` — Set FederationIdentifier on SF users
- `scripts/setup-sf-org.py` — Consolidated SF org setup orchestrator
- `scripts/setup-sf-demo-user.py` — Demo user + custom profile + test data

### OBO Prerequisites (Salesforce side)
1. Create Connected App with JWT Bearer flow enabled (`scripts/setup-sf-obo-eca.py`)
2. Upload X.509 certificate (public key) to the Connected App (UI-only for PKCE; metadata for cert)
3. Set OAuth Policies → "Admin approved users are pre-authorized" (via SetupEntityAccess API)
4. Assign profiles for allowed users
5. Set FederationIdentifier on each SF user = their Azure AD `oid` (`scripts/set-sf-federation-id.py`)
6. Import PFX (private key + cert) into Azure Key Vault as `sf-jwt-bearer`

### OBO Prerequisites (Azure side)
1. Key Vault with PFX certificate uploaded
2. APIM managed identity with "Key Vault Secrets User" RBAC role on the Key Vault
3. Certificate thumbprint set in `SF_JWT_BEARER_CERT_THUMBPRINT` env var

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
| 502 "SF Service Token Failed" | Bad cert, wrong client ID, or service account not pre-authorized | Verify cert thumbprint, client ID, and profile assignment |
| 403 "User Not Mapped" | No SF user with matching FederationIdentifier | Run `set-sf-federation-id.py` for the user |
| 502 "SF Token Exchange Failed" | Target SF user not pre-authorized for the Connected App | Assign user's profile to the Connected App via SetupEntityAccess |
| 500 (KeyNotFoundException) | Certificate thumbprint wrong or missing Named Value | Verify `SF_JWT_BEARER_CERT_THUMBPRINT` matches actual cert |
| "Missing required query parameter: audience" | `audience` missing on Foundry connection | Add `audience: 'https://ai.azure.com'` to connection properties |

### SF Org Setup (after new Dev Trial)
```bash
python scripts/setup-sf-org.py --org <alias> --email <admin-email>
```
