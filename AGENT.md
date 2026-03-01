# AGENT.md

This file provides guidance to code agents when working with this repository.

## Project Overview

**Salesforce MCP OBO** — fork of `meta-tool-salesforce` with **On-Behalf-Of (JWT Bearer) identity propagation**. User authenticates once to Azure AD; APIM exchanges the Azure AD token for a Salesforce token server-side via JWT Bearer flow. No Salesforce consent required. No dual auth. True OBO.

**Architecture (OBO mode):** Chat App (FastAPI + MSAL.js) → AI Foundry Agent → APIM (validates Azure AD JWT → exchanges for SF token via JWT Bearer → caches) → Salesforce MCP Server (FastMCP) → Salesforce APIs

**Architecture (OAuth2 mode — preserved):** Chat App → AI Foundry Agent → ApiHub (SF PKCE consent) → APIM (validates SF JWT) → MCP Server → Salesforce APIs

## Auth Modes

| Mode | `SF_AUTH_MODE` | User Auth | Token Exchange | Status |
|------|---------------|-----------|----------------|--------|
| OAuth2/PKCE | `oauth2` (default) | Azure AD + SF consent | ApiHub manages SF tokens | Working (inherited) |
| OBO/JWT Bearer | `obo` | Azure AD only | APIM exchanges via JWT Bearer | **New — in progress** |

## Development Quick Reference

### Deploy (OBO mode)
```bash
azd env new obo
azd env set SF_AUTH_MODE obo
azd env set SF_INSTANCE_URL "https://your-org.my.salesforce.com"
azd env set SF_CONNECTED_APP_CLIENT_ID "<obo-eca-consumer-key>"
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

### Required Environment Variables

| Variable | OAuth2 | OBO | Description |
|----------|--------|-----|-------------|
| `SF_AUTH_MODE` | `oauth2` (default) | `obo` | Auth flow selection |
| `SF_INSTANCE_URL` | Required | Required | SF org My Domain URL |
| `SF_CONNECTED_APP_CLIENT_ID` | Required | Required | ECA consumer key (different ECA per mode) |
| `SF_CONNECTED_APP_CLIENT_SECRET` | Required | Not needed | ECA consumer secret |

### Key Paths
- `src/salesforce-mcp/` — MCP server (6 tools, bearer passthrough) — **unchanged**
- `src/chat-app/` — FastAPI + MSAL.js frontend — **unchanged**
- `infra/` — Bicep IaC (main.bicep + modules/)
- `infra/modules/apim-sf-mcp-obo.bicep` — **NEW: OBO APIM API + Named Values**
- `infra/modules/sf-obo-connection.bicep` — **NEW: Foundry AAD connection**
- `infra/policies/sf-mcp-obo-policy.xml` — **NEW: OBO exchange policy**
- `infra/policies/sf-mcp-obo-prm-policy.xml` — **NEW: PRM for OBO**
- `hooks/postprovision.py` — Entra app + agent + connection setup (auth-mode-aware)
- `scripts/` — Setup, consent, and test scripts

### OBO Prerequisites (Salesforce side)
1. Create/configure SF External Client App with JWT Bearer flow enabled
2. Upload X.509 certificate (public key) to the SF ECA
3. Set OAuth Policies → "Admin approved users are pre-authorized"
4. Assign profiles/permission sets for allowed users
5. Set FederationIdentifier on each SF user = their Azure AD `oid`
6. Import PFX (private key + cert) into Azure Key Vault as `sf-jwt-bearer`

### SF Org Setup (after new Dev Trial)
```bash
python scripts/setup-sf-org.py --org <alias> --email <admin-email>
```
