# Salesforce Meta-Tool MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![azd compatible](https://img.shields.io/badge/azd-compatible-blue.svg)](https://learn.microsoft.com/azure/developer/azure-developer-cli/)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)

**6 tools. Fixed token cost. Your entire Salesforce org — with the user's own identity enforced end-to-end.**

A metadata-driven MCP server for Salesforce that lets an AI agent discover objects, learn field schemas, and construct SOQL queries at runtime. No hardcoded objects. No predefined reports. No service account with admin access.

```
azd up   # deploys the full stack in ~15 minutes
```

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

> **You also need a Salesforce Connected App** (External Client Application) configured for OAuth2 with PKCE. See [Setting Up Salesforce](#setting-up-salesforce) below.

### Deploy

```bash
git clone https://github.com/ozgurkarahan/salesforce-meta-tool.git
cd salesforce-meta-tool

azd env set SF_INSTANCE_URL "https://your-org.my.salesforce.com"
azd env set SF_CONNECTED_APP_CLIENT_ID "<consumer-key>"
azd env set SF_CONNECTED_APP_CLIENT_SECRET "<consumer-secret>"

azd up
```

`azd up` deploys the full stack: Container Apps Environment, APIM Gateway, AI Foundry project, Chat App, Salesforce MCP server, OAuth connections, and the Foundry agent — all via Bicep. First deployment takes ~15 minutes.

### First Use

After `azd up` completes:

1. Configure the callback URL — adds the ApiHub redirect URI to your Salesforce Connected App:
   ```bash
   python scripts/configure-sf-connected-app.py
   ```
2. Open the Chat App at the URL printed at the end of `azd up`. Sign in with your Azure AD account.
3. Send a message (e.g., *"Show me my Salesforce accounts"*). On first use, the agent triggers an OAuth consent flow — click the consent link, authenticate with Salesforce, and the agent retries automatically.

For headless or CI environments:
```bash
python scripts/grant-sf-mcp-consent.py
```

---

## The Idea: Meta-Tool Pattern

Most Salesforce MCP servers define one tool per object (`get_accounts`, `get_opportunities`, …). That approach doesn't scale — an org with 100 custom objects needs 100 tools.

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

The server is a thin metadata-driven bridge: the agent calls `describe_object("Account")`, learns the schema, and constructs the SOQL query — instead of calling a hardcoded `get_accounts()` function.

---

## The 6 Tools — 1,235 Tokens for All of Salesforce

The entire tool surface fits in less than 1% of a 128K context window, and the cost is **fixed** regardless of org size.

| Tool | Tokens | What it does |
|------|--------|--------------|
| `list_objects` | 117 | Discover objects (1000+ in a typical org) — filter by name/label |
| `describe_object` | 109 | Field schemas, types, required flags, picklists, external IDs |
| `soql_query` | 225 | Full SOQL: relationships, aggregates, GROUP BY, auto-pagination |
| `search_records` | 175 | SOSL full-text search across multiple objects simultaneously |
| `write_record` | 226 | Create, update, upsert (by external ID), delete |
| `process_approval` | 129 | Submit, approve, reject — Salesforce approval workflows |
| **Server instructions** | **254** | Workflow guidance, conventions, when-to-use-which-tool |
| **Total** | **1,235** | **All objects, all fields, all operations** |

Compare with the alternatives:

| Approach | Token cost | Coverage |
|----------|------------|----------|
| Full OpenAPI spec | 5,000–15,000 | Hundreds of endpoints, most irrelevant |
| RAG documentation chunks | 2,000–10,000 | Partial, depends on retrieval quality |
| One tool per object | ~500 × N objects | Scales linearly, N can be 100+ |
| **This MCP server** | **1,235 fixed** | **All objects, all fields, all operations** |

> **Note:** The 1,235 tokens cover tool definitions. In practice, each `describe_object` call returns field schemas at runtime — a complex multi-step workflow will consume additional context. This is intentional: schemas are loaded on demand rather than pre-loaded into the system prompt.

### Tool Reference

**`list_objects`** — Entry point. Filters by name or label to find the right object among 1,000+. Returns name, label, and CRUD capability flags. Think `ls`.

**`describe_object`** — Schema inspector. Returns every field with its API name, data type, required flag, picklist values, relationships, and external ID flags. The agent calls this *before* writing. Think `man`.

**`soql_query`** — Precision read tool. Supports the full SOQL syntax: relationship queries, aggregates, `GROUP BY`, `HAVING`, date functions, subqueries. Auto-paginates at Salesforce's 2,000-record limit. Think `SQL`.

**`search_records`** — Discovery tool. SOSL full-text search across multiple objects simultaneously — useful when the agent doesn't know *which* object contains the data. Think `rg`.

**`write_record`** — Mutation tool. Four operations: `create`, `update`, `upsert` (by external ID), `delete`. Validates field names against the schema before calling the API — catches typos before they reach Salesforce. Think `echo >` or `rm`.

**`process_approval`** — Workflow tool. Submit records for approval, approve or reject pending work items. Integrates with Salesforce's built-in approval workflows. Think `git push` — a governed state transition.

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
│(browser) │JWT│  Agent       │JWT│      │JWT│  MCP Server   │JWT│ REST API   │
└──────────┘   └──────────────┘   └──────┘   └───────────────┘   └────────────┘
     │                                                                   │
     └─────────────── same user identity, same permissions ──────────────┘
```

The user's Salesforce OAuth token flows through every layer — untouched, unescalated. The MCP server never stores tokens. The Salesforce API enforces the same CRUD permissions, field-level security, sharing rules, and approval workflows that apply when the user logs into the Salesforce UI directly.

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

Identity propagation prevents privilege escalation — the agent can't do more than the user. It does not prevent the agent from misunderstanding intent. A query like *"delete all closed-lost opportunities from 2023"* is valid SOQL that will execute with full authorization if the user has delete rights.

For production use, treat destructive operations with the same care you'd apply to any irreversible action: confirmation prompts, audit logging, and appropriate permission scoping in Salesforce.

---

## Architecture

```
flowchart LR
    Browser["Browser\nMSAL.js"]
    ChatApp["Chat App\nFastAPI"]
    Agent["AI Foundry Agent\ngpt-4o + MCP tools"]
    APIM["APIM Gateway\nvalidate-jwt / RFC 9728 PRM"]
    MCP["Salesforce MCP\n6 tools · FastMCP\nbearer passthrough"]
    SF["Salesforce\nREST API"]

    Browser -->|"Azure AD token"| ChatApp
    ChatApp -->|"UserTokenCredential"| Agent
    Agent -->|"MCP protocol"| APIM
    APIM -->|"SF bearer token"| MCP
    MCP -->|"user's SF token"| SF
```

The MCP server is **stateless** — it never stores, caches, or refreshes tokens.

### How the Token Flows

1. User signs in via MSAL.js → gets an Azure AD token
2. Chat App passes the token to AI Foundry as a `UserTokenCredential`
3. AI Foundry Agent hits the Salesforce MCP tool → triggers OAuth consent (first time only)
4. User authenticates with Salesforce via OAuth2 + PKCE
5. ApiHub stores the Salesforce token on the Foundry project connection
6. Agent calls MCP server — APIM validates the SF JWT (`validate-jwt` with OIDC discovery)
7. MCP server receives the bearer token via middleware, passes it directly to the Salesforce REST API
8. Salesforce enforces permissions based on the authenticated user's profile

See [`docs/reauth-consent-flow.md`](docs/reauth-consent-flow.md) for the full OAuth + PKCE consent sequence.

---

## Project Structure

```
salesforce-meta-tool/
├── azure.yaml                    # azd project: 2 services (chat-app, salesforce-mcp)
├── src/
│   ├── salesforce-mcp/
│   │   ├── app.py                # The MCP server — 6 tools, bearer passthrough
│   │   └── salesforce_client.py  # Async Salesforce REST client with auth
│   └── chat-app/
│       ├── app.py                # FastAPI backend — MSAL → Foundry agent bridge
│       └── static/               # Vanilla JS SPA with MSAL.js
├── infra/
│   ├── main.bicep                # Orchestrator — all Azure resources
│   └── modules/                  # Modular Bicep (APIM, Container Apps, AI Foundry, …)
├── hooks/
│   └── postprovision.py          # Creates Entra app + Foundry agent + OAuth connections
├── scripts/                      # Setup, consent, and test scripts
└── docs/                         # Reauth flow documentation + diagrams
```

### Scripts

| Script | Purpose |
|--------|---------|
| `configure-sf-connected-app.py` | Adds ApiHub redirect URI to SF Connected App callback URLs |
| `grant-sf-mcp-consent.py` | Direct OAuth consent flow (bypasses ApiHub — for headless setups) |
| `test-salesforce-mcp.py` | 11-step end-to-end Salesforce MCP server validation |
| `test-agent-oauth.py` | Interactive multi-turn agent test with OAuth consent + MCP |
| `sf-auth-code.py` | Quick SF authorization code flow for debugging |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SF_INSTANCE_URL` | Yes | Your Salesforce org URL (e.g., `https://myorg.my.salesforce.com`) |
| `SF_CONNECTED_APP_CLIENT_ID` | Yes | Consumer Key from the Salesforce Connected App |
| `SF_CONNECTED_APP_CLIENT_SECRET` | Yes | Consumer Secret from the Salesforce Connected App |
| `COGNITIVE_ACCOUNT_SUFFIX` | No | Increment after `azd down --purge` to avoid "Project not found" errors (default: empty) |
| `AZURE_LOCATION` | No | Azure region (default: `swedencentral`) |

---

## Setting Up Salesforce

For a new Salesforce Developer org:

1. Sign up at [developer.salesforce.com/signup](https://developer.salesforce.com/signup)
2. Create an External Client App in Salesforce Setup:
   - Navigate to **Setup → App Manager → New Connected App**
   - Enable **OAuth Settings**
   - Add OAuth scopes: `api`, `refresh_token`
   - Leave the Callback URL empty for now (the configure script adds it)
   - Save, then copy the **Consumer Key** and **Consumer Secret**
3. Set the credentials:
   ```bash
   azd env set SF_INSTANCE_URL "https://your-org.my.salesforce.com"
   azd env set SF_CONNECTED_APP_CLIENT_ID "<consumer-key>"
   azd env set SF_CONNECTED_APP_CLIENT_SECRET "<consumer-secret>"
   ```
4. Deploy with `azd up`
5. Run `python scripts/configure-sf-connected-app.py` to add the callback URL

> The Connected App's Consumer Key is not retrievable via API — copy it from Salesforce Setup manually.

---

## Troubleshooting

| Problem | Solution |
|---------|---------|
| "Project not found" after `azd down` | Increment `COGNITIVE_ACCOUNT_SUFFIX` (e.g., `azd env set COGNITIVE_ACCOUNT_SUFFIX "2"`) and redeploy |
| APIM returns 401 Unauthorized | Salesforce token expired (2h TTL) — click **Re-authenticate** in the Chat App |
| Agent responds without calling tools | OAuth consent didn't complete — look for the consent banner in the chat |
| APIM breaks MCP streaming | Set response body bytes to `0` in APIM diagnostics (All APIs scope) |
| `validate-jwt` issuer mismatch | Use your org-specific instance URL, not `login.salesforce.com` |
| `configure-sf-connected-app.py` fails | Ensure `azd up` has run and Salesforce credentials are set |

---

## Current Scope and Limitations

This project is a proof of concept. Before using in production, consider:

- **Destructive operations**: There are no confirmation prompts or audit logs on `write_record` delete operations. Add guardrails appropriate to your org's governance requirements.
- **Token expiry mid-workflow**: Salesforce tokens have a 2-hour TTL. Long-running agentic workflows will need a re-authentication strategy.
- **Azure-specific infrastructure**: The deployment stack (APIM, AI Foundry, Container Apps) is Azure-native. Adapting this pattern to other clouds or self-hosted models requires replacing the infrastructure layer.
- **Rate limits**: The Salesforce REST API has per-org API call limits. High-frequency agentic workflows should account for this.

---

## Contributing

Contributions are welcome. Please open an [issue](https://github.com/ozgurkarahan/salesforce-meta-tool/issues) or submit a pull request.

This project uses `azd` for deployment — see [Quick Start](#quick-start) to get a local environment running.

---

## License

[MIT License](LICENSE)

---

*Related article: [The Meta-Tool Pattern Applied to Enterprise](https://www.linkedin.com/pulse/billion-dollar-agent-loop-ozgur-karahan-fszae/) on LinkedIn.*
