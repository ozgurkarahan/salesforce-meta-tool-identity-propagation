# Project Reference — Salesforce MCP OBO

All project-specific technical details. Referenced from [`CLAUDE.md`](../CLAUDE.md).
See also: [`obo-plan.md`](obo-plan.md) for the full OBO implementation plan.

---

## IaC Principle: Bicep First

Always prioritize Bicep for Azure resource creation. The post-provision hook (`hooks/postprovision.py`) is for:
- **Step 0: Certificate upload + APIM binding** — uploads `certs/sf-jwt-bearer.pfx` to Key Vault, creates APIM cert binding via ARM REST, sets `SF_JWT_BEARER_CERT_THUMBPRINT`. Bicep cert module is conditional (`!empty(sfJwtBearerCertThumbprint)`) to allow first deploy without cert.
- **Step 1: Entra App Registration** (Chat App SPA) — Graph Bicep extension requires `Application.ReadWrite.All` on the ARM deployment identity, unavailable in managed tenants
- **Step 2: SF OBO connection** — `ensure_obo_connection()`: OAuth2 identity passthrough (create-only, never overwrites; registers the connection redirectUrl on the MCP OAuth app). Not Bicep because the connection needs a client secret + per-connection redirect registration
- **Step 2b: Foundry Agent** — no ARM resource type; SDK only
- **Step 5: Agent Application** — ARM control plane (`Microsoft.CognitiveServices/accounts/projects/applications`), extracts `msaAppId`
- **Step 6: Agent Deployment** — ARM control plane, links agent version to application
- **Step 7: Bot Service bootstrap** — first-run only via ARM REST; detects existing bots by msaAppId and adopts them. Bicep takes over on subsequent deploys.
- **Step 8: Teams org catalog publish** — Graph API, requires `AppCatalog.ReadWrite.All` + Teams admin policy enabled

## Development Notes

### Environment

- **Platform:** Windows 11 + Git Bash
- **Python:** Use `python` not `python3` (Windows)
- **MSYS path fix:** `export MSYS_NO_PATHCONV=1` before `az` commands with resource ID paths
- **ACR builds:** `az acr build --no-logs` avoids charmap encoding errors on Windows

### Foundry SDK (`azure-ai-projects` v2 beta — Responses API)

- `AIProjectClient` from `azure-ai-projects` — connects to the project endpoint
- Agent creation: `project_client.agents.create_version()` with `PromptAgentDefinition` + `MCPTool`
- Agent execution: `project_client.get_openai_client()` → `openai_client.responses.create()`
- `MCPTool`: `server_label`, `server_url`, `require_approval`, `allowed_tools`, `project_connection_id`
- Tool approval: `require_approval: "never"` — SDK-level approval disabled (breaks in Teams). System prompt guardrail handles write confirmation conversationally.
- `server_label` must match `^[a-zA-Z0-9_]+$` — no hyphens
- `gpt-5.4` (GlobalStandard) — model used for MCP tool support
- Agent name: `salesforce-assistant` (default, configurable via `AGENT_NAME` or dynamic discovery)
- `AIProjectClient.agents.list()` — lists all agents in a project (used for dynamic discovery)
- `create_foundry_client(access_token, project_endpoint)` — supports per-request project override
- `call_agent(..., agent_name, project_endpoint)` — targets any agent in any project

### Salesforce MCP Server

- `src/salesforce-mcp/` — FastMCP server with 7 tools: `whoami`, `list_objects`, `describe_object`, `soql_query`, `search_records`, `write_record`, `process_approval`
- Container App `ca-sf-mcp`, port 8000, tagged `azd-service-name: salesforce-mcp`
- `streamable_http_app()` serves MCP at `/mcp` — endpoint must include `/mcp` suffix
- **Bearer passthrough:** `contextvars.ContextVar` + Starlette `BaseHTTPMiddleware` extracts bearer token, `SalesforceClient._request()` uses it directly
- APIM uses native `type: 'mcp'` with `sf-mcp-backend` backend resource (migrated from `apiType: 'http'` on 2026-03-18)
- Metadata caching: 15-min TTL on describe results; SOQL pagination via `query_more()`

### Salesforce MCP Auth (APIM Token Validation)

- `validate-jwt` (NOT `validate-azure-ad-token`) — SF tokens are not Entra tokens
- SF JWT: `tty: "sfdc-core-token"`, RS256, `iss`/`aud` = org instance URL
- OIDC discovery: `{{SfInstanceUrl}}/.well-known/openid-configuration`
- Named Values: `SfInstanceUrl`, `APIMGatewayURL`
- RFC 9728 PRM at `salesforce-mcp/.well-known/oauth-protected-resource`
- ApiHub uses PKCE for SF OAuth — SF Connected App must have PKCE required (`isCodeCredentialFlowWithPKCE: true`) so both sides validate the code_verifier/code_challenge handshake

### Salesforce OAuth Connection

- RemoteTool + ApiHub pattern with OAuth2
- OAuth endpoints: `login.salesforce.com/services/oauth2/authorize` and `/token`
- Scopes: `["api", "refresh_token"]`
- SF Connected App needs ApiHub redirect URI in callback URLs
- Required env vars: `SF_CONNECTED_APP_CLIENT_ID`, `SF_CONNECTED_APP_CLIENT_SECRET`, `SF_INSTANCE_URL`
- Bicep deploys the connection with real SF credentials from azd env vars (no placeholders)
- **IMPORTANT:** Bicep-created connections do NOT register the ApiHub connector. The postprovision hook DELETE+PUTs the connection via ARM REST to trigger ApiHub setup.
- After `azd up` + postprovision, the first agent call triggers `oauth_consent_request` — user completes the native ApiHub PKCE consent flow in the browser to authorize Salesforce access. This works correctly.
- **Optional fallback:** `python scripts/grant-sf-mcp-consent.py` does a direct OAuth flow (no PKCE) and stores the refresh token via DELETE+PUT. Useful for headless/automated setups.
- SF tokens expire after 2h. Chat app's "Re-authenticate" button DELETE+PUTs the connection, triggering a fresh consent flow on the next request.

### Chat App (Multi-Agent Headless)

- `src/chat-app/` — FastAPI backend + vanilla JS SPA with MSAL.js
- **Multi-agent architecture** — agent-agnostic, works with any AI Foundry agent/project
- `UserTokenCredential` wraps the user's MSAL token for the Foundry SDK
- Token audience: `aud=https://ai.azure.com`, scope `user_impersonation`

**Endpoints:**
- `GET /api/config` — MSAL config
- `GET /api/agents` — Dynamic agent discovery (auth required). Priority: Resource Graph → `AGENTS_CONFIG` → `AGENT_NAME`
- `POST /api/chat` — Send message. Accepts `agent_name` + `project_endpoint` to target any agent
- `POST /api/chat/approve` — Tool approval. Also accepts `agent_name` + `project_endpoint`
- `GET /api/debug/logs` — SSE log stream
- `POST /api/messages` — Teams Bot Framework

**Dynamic Agent Discovery (GET /api/agents):**
1. Azure Resource Graph query (`microsoft.cognitiveservices/accounts/projects`) using managed identity
2. `AIProjectClient.agents.list()` per project (managed identity, `DefaultAzureCredential`)
3. Results cached 5 min per deployment
4. Fallback: `AGENTS_CONFIG` env var (static JSON), then single `AGENT_NAME`

**RBAC for discovery:**
- Container App managed identity needs `Reader` at subscription level (Resource Graph)
- Container App managed identity needs `Cognitive Services User` on each AI Services account (agent listing)
- Both are in Bicep: `subscription-role-assignment.bicep` (Reader) + `role-assignment.bicep` (Cognitive Services User)

**Frontend agent selector:**
- `AGENT_METADATA` mapping in `app.js` — enriches dynamic agents with icons and business prompts
- Project labels shown when multiple projects discovered
- Single-agent auto-selects (no selector UI)
- Agent badge in header shows active agent

### APIM Diagnostics (MCP Compatibility)

- **CRITICAL:** Response body bytes MUST be `0` at All APIs scope — breaks MCP SSE streaming
- Request body logging (8192 bytes) is fine — only response body logging causes issues

### Scripts

**Setup (run in order for new org, or use `setup-sf-org.py` to chain all):**
- `scripts/setup-sf-org.py` — Consolidated orchestrator: chains SSO + ECA + callback + demo user
- `.claude/scripts/setup-salesforce-sso.py` — Setup Salesforce SSO with Azure AD OIDC federation
- `scripts/setup-sf-external-client-app.py` — Create External Client App + OAuth settings via Metadata API
- `scripts/configure-sf-connected-app.py` — Add ApiHub redirect URI to ECA's callback URLs
- `scripts/setup-sf-demo-user.py` — Demo user + custom profile (no Account delete) + test data

**Testing & Utilities:**
- `scripts/test-salesforce-mcp.py` — 11-step end-to-end Salesforce MCP test
- `scripts/test-agent-oauth.py` — Interactive multi-turn agent test (OAuth consent + MCP approval)
- `scripts/grant-sf-mcp-consent.py` — OAuth consent for Salesforce MCP connection
- `scripts/sf-auth-code.py` — Quick SF authorization code flow for testing

### Deployment Caveats

- After `azd down --purge`, increment `COGNITIVE_ACCOUNT_SUFFIX` to avoid "Project not found" errors
- First deploy works in a single `azd up` pass — cert module is conditional, postprovision hook uploads cert to KV
- `certs/sf-jwt-bearer.pfx` must exist locally for auto-upload; see `docs/installation.md` Phase 1
- `SF_JWT_BEARER_CERT_THUMBPRINT` is auto-set by the postprovision hook — no manual thumbprint step needed
- Identifier URI format: managed tenant requires `api://{appId}`
- SF JWT uses org-specific instance URL for `iss`/`aud` — NOT `login.salesforce.com`
- `SF_INSTANCE_URL` must be set via `azd env set` before `azd up` for APIM `validate-jwt` to work
- `main.bicepparam` must explicitly map azd env vars to Bicep parameters

---

## OBO (JWT Bearer) Flow — Verified

### Token Flow: Two-Hop Chain (Chat App → Foundry → APIM)

Foundry acquires its own token — it does NOT pass through the Chat App's MSAL token. Proven via Entra ID sign-in logs:

```
User (browser)
  │
  ├─[MSAL.js]──► Azure AD ──► token(aud=AzureML, appid=ChatApp)
  │                                │
  │                                ▼
  ├───────────────────────► AI Foundry (Responses API)
                                   │
                                   ├─[OAuth2 passthrough]─► Azure AD ─► token(aud=api://McpOauthApp)
                                   │                                        │
                                   │                                        ▼
                                   ├────────────────────────────────► APIM (validate-jwt)
                                                                           │
                                                                     [OBO exchange]
                                                                           │
                                                                           ▼
                                                                     SF MCP Server
```

**Hop 1 — User → Chat App → Foundry:** MSAL.js acquires a token with `aud = Azure Machine Learning Services` and `appid = Chat App` (`2a7cb5b6-...`). This token authenticates to Foundry, not to APIM.

**Hop 2 — Foundry → APIM:** Foundry's internal OAuth client (`propagate-id-entra`, `appid = 4659381e-...`) acquires a **separate** token with `aud` matching the MCP Gateway audience (`4438785f-...`). The user's identity (`oid`, `upn`) is preserved — Foundry performs an OBO-like exchange internally. In v1 the `UserEntraToken` connection config (`audience: 'https://ai.azure.com'`) told Foundry what audience to request. In v2 the `salesforce-obo-oauth2` connection (OAuth2 identity passthrough, scopes `api://<MCP_OAUTH_CLIENT_ID>/access_as_user` + `offline_access`) makes Foundry run an auth-code flow against our own Entra app — required since Foundry blocks Microsoft-audience tokens to custom MCP endpoints ([MS Learn](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication#oauth-identity-passthrough)).

**Key implication:** The `appid` arriving at APIM is Foundry's client ID, not the Chat App's.

### How It Works (Three-Phase Token Exchange)

**Phase 0 — Validate Azure AD token:**
APIM `validate-jwt` checks the user's Entra token (both v1 `sts.windows.net` and v2 `login.microsoftonline.com` issuers accepted, audience `https://ai.azure.com`). Extracts user identity via `{{IdentityClaimName}}` claim (default: `oid`).

**Phase 1 — Resolve SF username:**
SF JWT Bearer **always** requires `sub` = SF Username (not FederationIdentifier). Org-level SSO does not change this — only the SAML Bearer Assertion flow (a different grant type) supports FederationIdentifier matching. APIM checks cache for `sf-username-{oid}`. On miss: obtains a service token via JWT Bearer for `{{SfServiceAccountUsername}}`, then runs SOQL query to map `FederationIdentifier` → `Username`. Caches mapping for 1 hour.

**Phase 2 — Get SF user token:**
Checks cache for `sf-token-{username}`. On miss: creates JWT Bearer assertion with `sub = SF username`, signs with Key Vault certificate (referenced by thumbprint via `{{SfJwtBearerCertThumbprint}}`), exchanges at SF token endpoint. Caches for 30 minutes.

**Phase 3 — Forward:**
Replaces `Authorization` header with SF access token, forwards to MCP backend.

### Why Not SAML Bearer Assertion?

The SAML Bearer Assertion flow (`urn:ietf:params:oauth:grant-type:saml2-bearer`) could match `NameIdentifier` against `FederationIdentifier` directly — eliminating the service-token + SOQL-lookup phase. However, the complexity trade-off is unfavorable:

| Concern | JWT Bearer (current) | SAML Bearer Assertion |
|---|---|---|
| Assertion format | JSON (simple to construct in APIM) | XML with SAML 2.0 namespace, conditions, authn statement |
| Signing | RS256 on base64url JSON | XML Signature (enveloped, canonicalized) |
| APIM support | Native — C# string concat + RSA sign | No native support — XML canonicalization (C14N) + enveloped signature transforms not available |
| SF configuration | Connected App with certificate | Connected App + SAML SSO configuration + Identity Type setting |
| Org impact | None | Must configure SAML SSO org-wide |
| Caching overhead | Service token + SOQL lookup (cached, ~0ms warm) | None (single exchange) |

**Decision:** Keep the three-phase JWT Bearer approach. It's working, performant with caching, and uses only native APIM capabilities.

### OBO-Specific Infrastructure

| Resource | File | Named Value / Config |
|----------|------|---------------------|
| APIM OBO API | `apim-sf-mcp-obo.bicep` | Path: `/salesforce-mcp-obo`, type: `mcp`, backend: `sf-mcp-backend` |
| OBO Policy | `sf-mcp-obo-policy.xml` | Uses: `TenantId`, `SfOboClientId`, `SfOboLoginUrl`, `SfJwtBearerCertThumbprint`, `SfServiceAccountUsername`, `IdentityClaimName` |
| OBO PRM | `sf-mcp-obo-prm-policy.xml` | Uses: `APIMGatewayURL`, `TenantId` |
| Foundry Connection | `ensure_obo_connection()` in `hooks/postprovision.py` | `authType: OAuth2` (`salesforce-obo-oauth2`), scopes `api://<app>/access_as_user` + `offline_access` |
| KV Certificate | `apim-jwt-bearer-cert.bicep` | Referenced by thumbprint: `context.Deployment.Certificates["{{SfJwtBearerCertThumbprint}}"]` |
| KV RBAC | `keyvault.bicep` | Key Vault Secrets User for APIM managed identity |
| App Insights | `cognitive.bicep` | Account-level connection, shared to all projects |
| Subscription Reader | `subscription-role-assignment.bicep` | Chat App MI → Reader on subscription (Resource Graph discovery) |

### User Mapping

- Azure AD `oid` (immutable, same across all apps) → SF `FederationIdentifier`
- Each SF user must have their FederationIdentifier set to their Azure AD `oid`
- `oid` is NOT the same as `sub` — `sub` is pairwise per app registration
- FederationIdentifier must be unique per SF org

### SF Org Requirements

- Connected App (classic, not ECA) with JWT Bearer flow enabled + certificate uploaded via Metadata API
- OAuth Policies: "Admin approved users are pre-authorized" (via `isAdminApproved: true` in metadata + SetupEntityAccess API for profile assignment)
- Profiles assigned for allowed users
- Service account: `MCP_OBO_Service_Account` Permission Set (ApiEnabled + ViewAllUsers) assigned — does NOT require System Administrator profile
- Permission Set pre-authorized for the Connected App via SetupEntityAccess
- FederationIdentifier set on each SF user = their Azure AD `oid`

### Verified Behavior (2026-03-01)

- End-to-end OBO flow works: Azure AD auth → APIM exchange → SF API call → correct user identity
- Multi-user verified: two Azure AD users mapped to separate SF users, both authenticated successfully
- Foundry two-hop token chain confirmed via Entra sign-in logs (Chat App token ≠ APIM token)
- SF Login History shows per-user "Remote Access 2.0 / Success" entries
- Caching minimizes latency (warm user: ~0ms overhead)
- Error recovery (cache eviction on 401) is automatic
- v1: `UserEntraToken` connection type worked with `audience: https://ai.azure.com` — **dead since 2026-05-22** (`Cannot pass Microsoft token to untrusted MCP endpoint`); v2 uses OAuth2 identity passthrough (verified 2026-07-29, see CHANGELOG.md)

### Remaining Items

- Certificate rotation strategy (365-day expiry)
- Monitoring: App Insights connection auto-deployed via Bicep (needs verification after `azd up`)
- SAML SSO for browser login: **Implemented** (2026-03-29) — `step_sso` in setup-sf-org.py, no Apex needed
