# Salesforce Meta-Tool: Identity Propagation

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![azd compatible](https://img.shields.io/badge/azd-compatible-blue.svg)](https://learn.microsoft.com/azure/developer/azure-developer-cli/)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)

**One sign-on. Six tools. Your entire Salesforce org, with the user's own identity enforced end-to-end.**

A metadata-driven MCP server for Salesforce that lets an AI agent discover objects, learn field schemas, and construct SOQL queries at runtime, with true On-Behalf-Of identity propagation. The user authenticates once to Azure AD; the system handles the rest.

```
azd up   # deploys the full stack in ~15 minutes
```

---

## The Idea: Meta-Tool Pattern

Most Salesforce MCP servers define one tool per object (`get_accounts`, `get_opportunities`, …). That approach doesn't scale: an org with 100 custom objects needs 100 tools.

This project uses a different pattern, borrowed from how Claude Code works:

```
Developer World                    Enterprise World
─────────────────                  ─────────────────
Bash (meta-tool)          →        Salesforce MCP Server (meta-tool)
  └─ git, npm, docker               └─ list, describe, query, search, write, approve
     kubectl, terraform                 covers any object, any field, any workflow
```

**Bash doesn't implement git.** It delegates to git. The agent builds the command.

**This MCP server doesn't implement CRM logic.** It delegates to Salesforce. The agent builds the query.

The server is a thin metadata-driven bridge: the agent calls `describe_object("Account")`, learns the schema, and constructs the SOQL query, instead of calling a hardcoded `get_accounts()` function.

---

## Quick Start

### Prerequisites

| Requirement | Version | Link |
|-------------|---------|------|
| Azure subscription | — | [Free trial](https://azure.microsoft.com/free/) |
| Azure Developer CLI | 1.5+ | [Install azd](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) |
| Python | 3.11+ | [python.org](https://www.python.org/) |
| Docker Desktop | — | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Salesforce org | Developer or Sandbox | [developer.salesforce.com](https://developer.salesforce.com/signup) |

Single sign-on: user authenticates to Azure AD, APIM exchanges for a Salesforce token server-side via JWT Bearer.

```bash
git clone https://github.com/ozgurkarahan/salesforce-meta-tool-identity-propagation.git
cd salesforce-meta-tool-identity-propagation

azd env new obo
azd env set SF_INSTANCE_URL "https://your-org.my.salesforce.com"
azd env set SF_CONNECTED_APP_CLIENT_ID "<connected-app-consumer-key>"
azd env set SF_JWT_BEARER_CERT_THUMBPRINT "<certificate-thumbprint>"
azd env set SF_SERVICE_ACCOUNT_USERNAME "<svc@your-org.my.salesforce.com>"

azd up
```

> **Salesforce prerequisites:** Connected App with JWT Bearer flow enabled, certificate uploaded, service account with `MCP_OBO_Service_Account` Permission Set, FederationIdentifier set on each user. See [Setting Up Salesforce](#setting-up-salesforce) below.

### First Use

After `azd up` completes, open the Chat App at the URL printed at the end of deployment. Sign in with your Azure AD account and send a message (e.g., *"Show me my Salesforce accounts"*). OBO mode works immediately — no Salesforce consent required.

---

## The 6 Tools: 1,235 Tokens for All of Salesforce

The entire tool surface fits in less than 1% of a 128K context window, and the cost is **fixed** regardless of org size.

| Tool | Tokens | What it does |
|------|--------|--------------|
| `list_objects` | 117 | Discover objects (1000+ in a typical org), filter by name/label |
| `describe_object` | 109 | Field schemas, types, required flags, picklists, external IDs |
| `soql_query` | 225 | Full SOQL: relationships, aggregates, GROUP BY, auto-pagination |
| `search_records` | 175 | SOSL full-text search across multiple objects simultaneously |
| `write_record` | 226 | Create, update, upsert (by external ID), delete |
| `process_approval` | 129 | Submit, approve, reject via Salesforce approval workflows |
| **Server instructions** | **254** | Workflow guidance, conventions, when-to-use-which-tool |
| **Total** | **1,235** | **All objects, all fields, all operations** |

Compare with the alternatives:

| Approach | Token cost | Coverage |
|----------|------------|----------|
| Full OpenAPI spec | 5,000–15,000 | Hundreds of endpoints, most irrelevant |
| RAG documentation chunks | 2,000–10,000 | Partial, depends on retrieval quality |
| One tool per object | ~500 × N objects | Scales linearly, N can be 100+ |
| **This MCP server** | **1,235 fixed** | **All objects, all fields, all operations** |

> **Note:** The 1,235 tokens cover tool definitions. In practice, each `describe_object` call returns field schemas at runtime. A complex multi-step workflow will consume additional context. This is intentional: schemas are loaded on demand rather than pre-loaded into the system prompt.

### Tool Reference

**`list_objects`**: Entry point. Filters by name or label to find the right object among 1,000+. Returns name, label, and CRUD capability flags. Think `ls`.

**`describe_object`**: Schema inspector. Returns every field with its API name, data type, required flag, picklist values, relationships, and external ID flags. The agent calls this *before* writing. Think `man`.

**`soql_query`**: Precision read tool. Supports the full SOQL syntax: relationship queries, aggregates, `GROUP BY`, `HAVING`, date functions, subqueries. Auto-paginates at Salesforce's 2,000-record limit. Think `SQL`.

**`search_records`**: Discovery tool. SOSL full-text search across multiple objects simultaneously, useful when the agent doesn't know *which* object contains the data. Think `rg`.

**`write_record`**: Mutation tool. Four operations: `create`, `update`, `upsert` (by external ID), `delete`. Validates field names against the schema before calling the API, catches typos before they reach Salesforce. Think `echo >` or `rm`.

**`process_approval`**: Workflow tool. Submit records for approval, approve or reject pending work items. Integrates with Salesforce's built-in approval workflows. Think `git push`, a governed state transition.

---

## Identity Propagation: Why It Matters

The most common enterprise MCP pattern connects via a service account:

```
User → Agent → Service Account → Salesforce
                    ↑
        Admin access. Sees ALL data. Bypasses sharing rules.
        "List all opportunities" returns the entire pipeline.
```

This project propagates the user's own token through every layer:

```
┌──────────┐   ┌──────────────┐   ┌──────┐   ┌───────────────┐   ┌────────────┐
│  User    │──▶│  AI Foundry  │──▶│ APIM │──▶│  Salesforce   │──▶│ Salesforce │
│(browser) │   │  Agent       │   │      │   │  MCP Server   │   │ REST API   │
└──────────┘   └──────────────┘   └──────┘   └───────────────┘   └────────────┘
     │                                                                   │
     └─────────────── same user identity, same permissions ──────────────┘
```

The user's Salesforce token flows through every layer, untouched, unescalated. The MCP server never stores tokens. The Salesforce API enforces the same CRUD permissions, field-level security, sharing rules, and approval workflows that apply when the user logs into the Salesforce UI directly.

**The agent becomes a power tool, not a privileged backdoor.**

The entire identity propagation logic is seven lines:

```python
class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        tok = _request_token.set(token)
        try:
            return await call_next(request)
        finally:
            _request_token.reset(tok)
```

### What Identity Propagation Does Not Protect Against

Identity propagation prevents privilege escalation: the agent can't do more than the user. It does not prevent the agent from misunderstanding intent. A query like *"delete all closed-lost opportunities from 2023"* is valid SOQL that will execute with full authorization if the user has delete rights.

For production use, treat destructive operations with the same care you'd apply to any irreversible action: confirmation prompts, audit logging, and appropriate permission scoping in Salesforce.

---

## Architecture

```
User (browser)
  │
  ├─[MSAL.js]──► Azure AD ──► token(aud=AzureML, appid=ChatApp)
  │                                │
  │                                ▼
  ├───────────────────────► AI Foundry (Responses API)
                                   │
                                   ├─[UserEntraToken]──► Azure AD ──► token(aud=MCP-Gateway)
                                   │                                        │
                                   │                                        ▼
                                   ├────────────────────────────────► APIM (validate-jwt)
                                                                           │
                                                                     [Three-phase exchange]
                                                                           │
                                                                           ▼
                                                                     SF MCP Server
                                                                           │
                                                                           ▼
                                                                     Salesforce API
```

**Hop 1, User to Foundry:** MSAL.js acquires a token for the Chat App. The Chat App passes it to AI Foundry, which preserves the user's identity (`oid`, `upn`).

**Hop 2, Foundry to APIM:** Foundry's internal OAuth client acquires a separate token for the MCP Gateway audience. The user's identity is preserved through Foundry's On-Behalf-Of (OBO)-like exchange.

**Three-Phase Token Exchange (APIM Policy):**

1. **Phase 0, Validate:** APIM `validate-jwt` checks the Azure AD token (v1 and v2 issuers, audience `https://ai.azure.com`). Extracts user `oid`.
2. **Phase 1, Resolve SF username:** Checks cache for `sf-username-{oid}`. On miss: acquires a service token, runs `SELECT Username FROM User WHERE FederationIdentifier = '{oid}'`. Caches 1 hour.
3. **Phase 2, Get SF user token:** Checks cache for `sf-token-{username}`. On miss: creates JWT Bearer assertion with `sub = SF username`, signs with Key Vault certificate, exchanges at SF token endpoint. Caches 30 min.
4. **Phase 3, Forward:** Replaces `Authorization` header with the SF access token, forwards to MCP backend.

**Warm user overhead: ~0ms.** All three phases hit cache.

The MCP server is **stateless**. It never stores, caches, or refreshes tokens.

---

## Project Structure

```
salesforce-meta-tool-identity-propagation/
├── azure.yaml                    # azd project: 2 services (chat-app, salesforce-mcp)
├── src/
│   ├── salesforce-mcp/
│   │   ├── app.py                # The MCP server: 6 tools, bearer passthrough
│   │   └── salesforce_client.py  # Async Salesforce REST client with auth
│   └── chat-app/
│       ├── app.py                # FastAPI backend, MSAL to Foundry agent bridge
│       └── static/               # Vanilla JS SPA with MSAL.js
├── infra/
│   ├── main.bicep                # Orchestrator, all Azure resources
│   ├── main.bicepparam           # Environment variable → Bicep param mapping
│   ├── modules/
│   │   ├── apim.bicep            # APIM Gateway
│   │   ├── apim-sf-mcp-obo.bicep # OBO APIM API + Named Values
│   │   ├── apim-jwt-bearer-cert.bicep  # Key Vault → APIM certificate binding
│   │   ├── sf-obo-connection.bicep     # Foundry UserEntraToken connection
│   │   ├── keyvault.bicep        # Key Vault + APIM RBAC access
│   │   ├── cognitive.bicep       # AI Services, project, App Insights
│   │   └── ...                   # Container Apps, registry, monitoring, storage
│   └── policies/
│       ├── sf-mcp-obo-policy.xml     # OBO three-phase exchange policy
│       └── sf-mcp-obo-prm-policy.xml # RFC 9728 PRM for OBO endpoint
├── hooks/
│   └── postprovision.py          # Creates Entra app + Foundry agent + OBO connection
├── scripts/                      # Setup and test scripts
└── docs/                         # Architecture diagrams
```

### Scripts

| Script | Purpose |
|--------|---------|
| `setup-sf-org.py` | Consolidated SF org setup orchestrator: chains SSO, demo user, OBO service account |
| `setup-sf-obo-eca.py` | Creates SF Connected App for JWT Bearer via Metadata API |
| `set-sf-federation-id.py` | Sets FederationIdentifier on SF users (Azure AD `oid` → SF user) |
| `setup-sf-demo-user.py` | Creates demo user + custom profile (no Account delete) + test data |
| `setup-sf-service-account.py` | Creates dedicated OBO service account (Minimum Access profile + Permission Set) |
| `test-salesforce-mcp.py` | 11-step end-to-end Salesforce MCP server validation |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SF_INSTANCE_URL` | Yes | Salesforce org URL (e.g., `https://myorg.my.salesforce.com`) |
| `SF_CONNECTED_APP_CLIENT_ID` | Yes | Consumer Key from the Salesforce Connected App |
| `SF_JWT_BEARER_CERT_THUMBPRINT` | Yes | Certificate thumbprint for JWT Bearer signing |
| `SF_SERVICE_ACCOUNT_USERNAME` | Yes | SF service account username for SOQL user lookups |
| `SF_JWT_BEARER_CERT_NAME` | No | Key Vault certificate name (default: `sf-jwt-bearer`) |
| `IDENTITY_CLAIM_NAME` | No | Azure AD JWT claim for user identity (default: `oid`) |
| `COGNITIVE_ACCOUNT_SUFFIX` | No | Increment after `azd down --purge` to avoid naming conflicts |
| `AZURE_LOCATION` | No | Azure region (default: `swedencentral`) |

---

## Setting Up Salesforce

The OBO flow requires a Connected App with JWT Bearer flow, a certificate, and user-to-identity mapping.

### Salesforce Side

1. **Create a Connected App** with JWT Bearer flow enabled:
   ```bash
   python scripts/setup-sf-obo-eca.py --org <alias> --email <your-email> --cert certs/sf-jwt-bearer.crt
   ```
   This creates the Connected App via Metadata API, uploads the certificate, sets OAuth policies to "Admin approved users are pre-authorized", and creates a Permission Set (`MCP_OBO_Service_Account`) with minimal permissions (API Enabled + View All Users).

2. **Create a dedicated service account** for SOQL user lookups:
   ```bash
   python scripts/setup-sf-service-account.py --org <alias> --email <your-email>
   ```
   This creates a user with the most restrictive profile (`Minimum Access - Salesforce`), assigns the `MCP_OBO_Service_Account` Permission Set, and pre-authorizes it for the Connected App. The service account does not need System Administrator — the Permission Set provides the required permissions.

3. **Assign profiles.** Add the profiles of allowed users to the Connected App (via SetupEntityAccess API, handled by the setup-sf-obo-eca.py script).

4. **Map users.** Set `FederationIdentifier` on each SF user to their Azure AD `oid`:
   ```bash
   python scripts/set-sf-federation-id.py
   ```
   The `oid` claim is immutable and consistent across all Azure AD applications for a given user.

> **Why `oid` and not `sub`?** The `sub` claim is pairwise: it changes per app registration. `oid` is the same across all apps in the tenant, making it a stable identity anchor.

### Azure Side

1. **Upload the certificate** (PFX with private key) to Azure Key Vault as `sf-jwt-bearer`
2. **APIM managed identity** must have "Key Vault Secrets User" RBAC role on the Key Vault (deployed automatically by Bicep)
3. Set the certificate thumbprint:
   ```bash
   azd env set SF_JWT_BEARER_CERT_THUMBPRINT "<thumbprint>"
   ```

### Deploy

```bash
azd up
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Project not found" after `azd down` | Increment `COGNITIVE_ACCOUNT_SUFFIX` (e.g., `azd env set COGNITIVE_ACCOUNT_SUFFIX "2"`) and redeploy |
| APIM breaks MCP streaming | Set response body bytes to `0` in APIM diagnostics (All APIs scope) |
| Agent responds without calling tools | Connection misconfigured — check Foundry connection target URL |
| 401 "Invalid Azure AD token" | Token issuer/audience mismatch. Check `validate-jwt` issuers include both v1 and v2 |
| 502 "SF Service Token Failed" | Bad certificate, wrong client ID, or service account not pre-authorized. Verify `MCP_OBO_Service_Account` Permission Set is assigned |
| 403 "User Not Mapped" | No SF user with matching FederationIdentifier. Run `set-sf-federation-id.py` |
| 502 "SF Token Exchange Failed" | Target SF user not pre-authorized. Assign user's profile to the Connected App |
| 500 (KeyNotFoundException) | Certificate thumbprint wrong or Named Value missing. Verify `SF_JWT_BEARER_CERT_THUMBPRINT` |

---

## IdP Flexibility

The On-Behalf-Of (OBO) architecture is not locked to Azure AD. The `IdentityClaimName` Named Value (default: `oid`) controls which JWT claim is used for user identity. To switch to another IdP:

| What changes | Where | Notes |
|---|---|---|
| OIDC discovery URL | `sf-mcp-obo-policy.xml` | Point to PingFed, Okta, or other OIDC endpoint |
| Issuer validation | `sf-mcp-obo-policy.xml` | Update to new issuer(s) |
| Identity claim name | `IDENTITY_CLAIM_NAME` env var | `oid` → `sub` or a custom claim |
| Audience | `sf-mcp-obo-policy.xml` | Match IdP configuration |
| Foundry connection type | `sf-obo-connection.bicep` | `UserEntraToken` is Azure-only; other IdPs need `CustomKeys` |

The MCP server and Salesforce Connected App configuration remain unchanged. Only the APIM policy and Foundry connection need updating.

---

## Current Scope and Limitations

This project is a proof of concept. Before using in production, consider:

- **Destructive operations**: There are no confirmation prompts or audit logs on `write_record` delete operations. Add guardrails appropriate to your org's governance requirements.
- **Token expiry mid-workflow**: APIM caches tokens for 30 minutes and auto-evicts on 401. Long-running workflows may need to retry.
- **Certificate rotation**: The Key Vault certificate used for JWT Bearer signing has a default expiry of 365 days. Plan for rotation.
- **Azure-specific infrastructure**: The deployment stack (APIM, AI Foundry, Container Apps) is Azure-native. Adapting this pattern to other clouds or self-hosted models requires replacing the infrastructure layer, though the [IdP flexibility](#idp-flexibility) section shows the authentication layer is modular.
- **Rate limits**: The Salesforce REST API has per-org API call limits. High-frequency agentic workflows should account for this.

---

## Contributing

Contributions are welcome. Please open an [issue](https://github.com/ozgurkarahan/salesforce-meta-tool-identity-propagation/issues) or submit a pull request.

This project uses `azd` for deployment. See [Quick Start](#quick-start) to get a local environment running.

---

## License

[MIT License](LICENSE)

---

*Related article: [The Meta-Tool Pattern Applied to Enterprise](https://www.linkedin.com/pulse/billion-dollar-agent-loop-ozgur-karahan-fszae/) on LinkedIn.*
