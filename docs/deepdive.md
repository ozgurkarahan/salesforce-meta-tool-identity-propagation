# Deep Dive

Detailed technical content for the [Salesforce Meta-Tool: Identity Propagation](../README.md) project.

---

## Token Claim Glossary

These terms appear throughout the docs. Understanding the difference prevents the most common debugging confusion.

| Term | Source | Value | Used for |
|------|--------|-------|----------|
| Azure AD `oid` | Entra ID JWT | `d178a230-d9c1-...` (GUID) | Immutable user ID, same across all apps in the tenant. Default `IdentityClaimName` for OBO lookup. |
| Azure AD `sub` | Entra ID JWT | `a1b2c3d4-...` (GUID) | Pairwise — different per app registration. Do NOT use for cross-app identity mapping. |
| `preferred_username` | Entra ID JWT | `user@company.com` | User's UPN or email. Mutable — can change on name/domain change. Use with caution. |
| SF `FederationIdentifier` | Salesforce User record | Whatever the upstream IdP sends | Single field that links an external identity to a SF user. Must be unique per org. |
| SF JWT Bearer `sub` | JWT assertion payload | SF Username (e.g., `user@myorg.com`) | Must be the Salesforce Username — NOT the FederationIdentifier. This is the most common mistake. |

**Key insight:** The APIM OBO policy bridges two identity spaces: it uses `FederationIdentifier` (matched via `oid`) to *find* the SF Username, then uses that Username as `sub` in the JWT Bearer assertion. These are two separate values serving two different purposes.

---

## The Meta-Tool Pattern

Most Salesforce MCP servers define one tool per object (`get_accounts`, `get_opportunities`, ...). That approach doesn't scale: an org with 100 custom objects needs 100 tools.

This project uses a different pattern, borrowed from how Claude Code works:

```
Developer World                    Enterprise World
-----------------                  -----------------
Bash (meta-tool)          ->       Salesforce MCP Server (meta-tool)
  +- git, npm, docker                +- list, describe, query, search, write, approve
     kubectl, terraform                 covers any object, any field, any workflow
```

**Bash doesn't implement git.** It delegates to git. The agent builds the command.
**This MCP server doesn't implement CRM logic.** It delegates to Salesforce. The agent builds the query.

### How the Agent Thinks

The user asks: *"Show me my open opportunities worth over $100k"*

1. **Discover.** `list_objects(filter="Opportunity")` — finds the object and its CRUD flags.
2. **Learn.** `describe_object("Opportunity")` — gets every field: `Name`, `Amount`, `StageName` (with picklist values), `IsClosed`, and 60+ more.
3. **Query.** Builds SOQL from the schema:
   ```sql
   SELECT Name, Amount, StageName, CloseDate, Account.Name
   FROM Opportunity
   WHERE Amount > 100000 AND IsClosed = false
   ORDER BY Amount DESC
   ```
4. **Execute.** `soql_query(...)` returns *the user's own data*, filtered by their sharing rules and field-level security. A sales rep sees their pipeline. A VP sees the full forecast. Same query, different results.

### Why This Scales

The tool surface is **fixed**: six tools, 1,235 tokens. Whether the org has 50 objects or 5,000, the MCP server definition doesn't change.

| Approach | Token cost | Coverage |
|----------|------------|----------|
| Full OpenAPI spec | 5,000-15,000 | Hundreds of endpoints, most irrelevant |
| RAG documentation chunks | 2,000-10,000 | Partial, depends on retrieval quality |
| One tool per object | ~500 x N objects | Scales linearly, N can be 100+ |
| **This MCP server** | **1,235 fixed** | **All objects, all fields, all operations** |

---

## Tool Reference

**`list_objects`**: Entry point. Filters by name or label to find the right object among 1,000+. Returns name, label, and CRUD capability flags. Think `ls`.

**`describe_object`**: Schema inspector. Returns every field with its API name, data type, required flag, picklist values, relationships, and external ID flags. The agent calls this *before* writing. Think `man`.

**`soql_query`**: Precision read tool. Supports the full SOQL syntax: relationship queries, aggregates, `GROUP BY`, `HAVING`, date functions, subqueries. Auto-paginates at Salesforce's 2,000-record limit. Think `SQL`.

**`search_records`**: Discovery tool. SOSL full-text search across multiple objects simultaneously, useful when the agent doesn't know *which* object contains the data. Think `rg`.

**`write_record`**: Mutation tool. Four operations: `create`, `update`, `upsert` (by external ID), `delete`. Validates field names against the schema before calling the API, catches typos before they reach Salesforce. Think `echo >` or `rm`.

**`process_approval`**: Workflow tool. Submit records for approval, approve or reject pending work items. Integrates with Salesforce's built-in approval workflows. Think `git push`, a governed state transition.

---

## Identity Propagation: End-to-End

<p align="center"><img src="diagrams/obo-token-exchange.gif" width="600" alt="OBO Token Exchange — Detailed Identity Propagation"></p>

### The Problem

The most common enterprise MCP pattern connects via a service account:

```
User -> Agent -> Service Account -> Salesforce
                      ^
          Admin access. Sees ALL data. Bypasses sharing rules.
          "List all opportunities" returns the entire pipeline.
```

This project propagates the user's own identity through every layer. The Salesforce API enforces the same CRUD permissions, field-level security, sharing rules, and approval workflows that apply when the user logs into the Salesforce UI directly.

**The agent becomes a power tool, not a privileged backdoor.**

### Architecture

```
User (browser)
  |
  +-[MSAL.js]--> Azure AD --> token(aud=AzureML, appid=ChatApp)
  |                                |
  |                                v
  +--------------------------> AI Foundry (Responses API)
                                   |
                                   +-[UserEntraToken]--> Azure AD --> token(aud=MCP-Gateway)
                                   |                                        |
                                   |                                        v
                                   +--------------------------------------> APIM (validate-jwt)
                                                                           |
                                                                     [Three-phase exchange]
                                                                           |
                                                                           v
                                                                     SF MCP Server
                                                                           |
                                                                           v
                                                                     Salesforce API
```

### Every Hop, Every Token

Here is what happens when a user sends a message, traced through every authentication boundary:

**1. User signs in.** MSAL.js acquires an Azure AD token (`aud=AzureML`, claims include `oid` and `upn`). The `oid` (object ID) is the user's immutable identity across all Azure AD apps in the tenant.

**2. Chat App forwards to AI Foundry.** The Chat App passes the user's token to AI Foundry via `UserTokenCredential`. Foundry preserves the user's identity (`oid`, `upn`) through its internal OBO-like exchange.

**3. Foundry acquires an APIM-audience token.** Foundry's OAuth client acquires a separate token scoped to the MCP Gateway audience (`aud=https://ai.azure.com`). The user's `oid` and `upn` claims are carried forward.

**4. APIM validates the Azure AD JWT.** The `validate-jwt` policy checks the token against both v1 (`sts.windows.net`) and v2 (`login.microsoftonline.com`) issuers and the expected audience. The user's `oid` is extracted.

**5. APIM resolves the Salesforce username.** APIM checks its cache for `sf-username-{oid}`. On miss: a service account token runs `SELECT Username FROM User WHERE FederationIdentifier = '{oid}'` against Salesforce. The result is cached for 1 hour.

> **Why `oid` and not `sub`?** The `sub` claim is pairwise: it changes per app registration. `oid` is the same across all apps in the tenant, making it a stable identity anchor for the `FederationIdentifier` mapping.

**6. APIM gets the user's Salesforce token.** APIM checks its cache for `sf-token-{username}`. On miss: it creates a JWT Bearer assertion with `sub` = the Salesforce username, signs it with a Key Vault certificate, and exchanges it at the Salesforce token endpoint. The result is cached for 30 minutes.

**7. APIM forwards with the Salesforce token.** The original `Authorization` header is replaced with the user's Salesforce access token. The request continues to the MCP backend.

**8. MCP server extracts the token.** The entire identity propagation logic on the MCP side is seven lines:

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

**9. Salesforce enforces the user's own permissions.** Every API call runs as the mapped user. CRUD permissions, field-level security, sharing rules, and approval workflows all apply. A sales rep sees their pipeline. An admin sees everything. Same MCP server, different results.

### Caching and Performance

Three cache layers keep warm requests at ~0ms overhead:

| Cache key | TTL | What it stores |
|-----------|-----|---------------|
| `sf-service-token` | 30 min | Service account token for SOQL user lookups |
| `sf-username-{oid}` | 1 hour | Azure AD `oid` -> Salesforce username mapping |
| `sf-token-{username}` | 30 min | Per-user Salesforce access token |

On a 401 from Salesforce, APIM automatically evicts the cached user token. The next request re-exchanges transparently.

### Key Guarantees

- **No token stored.** The MCP server never persists, caches, or refreshes tokens. It reads the `Authorization` header and forwards it. Stateless.
- **No scope escalation.** The Salesforce token is scoped to the mapped user. APIM cannot mint a token with broader permissions than the user has in Salesforce.
- **Per-user audit trail.** Every Salesforce API call is logged under the user's own identity. The admin audit log shows *who* did *what*, not "the service account did everything."
- **Stateless MCP server.** No session state, no token cache, no user database. The server can be replaced, scaled, or restarted without affecting any user's session.

### What It Does Not Protect Against

Identity propagation prevents privilege escalation: the agent can't do more than the user. It does not prevent the agent from misunderstanding intent. For production use, treat destructive operations with the same care you'd apply to any irreversible action: confirmation prompts, audit logging, and appropriate permission scoping in Salesforce.

---

## IdP Flexibility

The On-Behalf-Of (OBO) architecture is not locked to Azure AD. The `IdentityClaimName` Named Value (default: `oid`) controls which JWT claim is used for user identity. To switch to another IdP:

| What changes | Where | Notes |
|---|---|---|
| OIDC discovery URL | `sf-mcp-obo-policy.xml` | Point to PingFed, Okta, or other OIDC endpoint |
| Issuer validation | `sf-mcp-obo-policy.xml` | Update to new issuer(s) |
| Identity claim name | `IDENTITY_CLAIM_NAME` env var | `oid` -> `sub` or a custom claim |
| Audience | `sf-mcp-obo-policy.xml` | Match IdP configuration |
| Foundry connection type | `sf-obo-connection.bicep` | `UserEntraToken` is Azure-only; other IdPs need `CustomKeys` |

The MCP server and Salesforce Connected App configuration remain unchanged. Only the APIM policy and Foundry connection need updating.

---

## Chained Federation: Multi-IdP Scenarios

In many enterprises, Salesforce SSO isn't federated directly with Entra ID. Instead, an SSO hub like PingFederate or Okta sits between them. This section explains how the identity propagation pattern works in those environments.

### The Universal "Join Key" Concept

Every IdP has a stable identifier that can serve as the join key to Salesforce's `FederationIdentifier` field:

| IdP | Stable identifier | Typical value | Notes |
|-----|-------------------|---------------|-------|
| Entra ID | `oid` | `d178a230-d9c1-...` | Immutable across all apps in the tenant |
| PingFederate | `NameID` (SAML) or `sub` (OIDC) | Configurable -- often UPN or employee ID | PingFed controls what gets emitted |
| Okta | `externalId` / `sub` | `00u1abc...` or UPN | Depends on Okta Universal Directory config |
| Salesforce | `FederationIdentifier` | Whatever the upstream IdP sends | Must be unique per org; matched during SSO |

The principle is always the same: **one stable claim from the IdP must match one field in Salesforce**. The question is which claim, and whether it survives transformation through intermediate IdPs.

### Pattern 1: Direct Federation

One IdP federates directly with Salesforce. The identity claim flows straight through.

```
Entra ID --[SAML/OIDC]--> Salesforce
  oid = "d178a230..."         FederationIdentifier = "d178a230..."
```

Whatever claim the IdP emits as `NameID` (SAML) or `sub` (OIDC) must match the SF user's `FederationIdentifier`. No transformation layer, no ambiguity.

### Pattern 2: Chained Federation (SSO Hub)

PingFederate (or Okta) acts as the SSO hub. Entra ID authenticates the user, but PingFed transforms claims before passing them to Salesforce.

```
Entra ID --[SAML]--> PingFederate --[SAML]--> Salesforce
  oid: "d178a230..."    NameID: ???              FederationIdentifier: ???
```

PingFederate is a **claim transformation layer**. It receives claims from Entra ID and can:

- **Pass through** the Entra `oid` as-is -- FederationIdentifier stores the oid
- **Map** to a different attribute (email, employee ID, custom) -- FederationIdentifier stores that
- **Enrich** from another source (LDAP, database lookup) -- FederationIdentifier stores the enriched value

The admin configures this in PingFed's **Authentication Source Mapping > Attribute Contract Fulfillment**. Whatever PingFed puts in `SAML_SUBJECT` is what Salesforce matches against `FederationIdentifier`.

### The FederationIdentifier Conflict

When SSO and API access (OBO) use different IdP paths, the `FederationIdentifier` can only hold one value. This creates a conflict:

| Path | Claim source | Claim value | FederationIdentifier must be... |
|------|-------------|-------------|-------------------------------|
| SSO (browser) | PingFed NameID | `EMP-12345` (employee ID) | `EMP-12345` |
| OBO (API) | Entra ID oid | `d178a230-d9c1-...` | `d178a230-d9c1-...` |

Both paths need to resolve to the same Salesforce user, but `FederationIdentifier` is a single field.

### Three Solutions

**Solution A -- Align on a common claim.** Configure PingFed to pass through the Entra ID `oid` unchanged. Both SSO and OBO use the same value. Simplest, but requires PingFed configuration change.

**Solution B -- Custom Salesforce field.** Keep `FederationIdentifier` for SSO. Add a custom field (e.g., `EntraOid__c`) for OBO lookup. Change the APIM SOQL query to:

```sql
SELECT Username FROM User WHERE EntraOid__c = '{oid}' AND IsActive = true LIMIT 1
```

No PingFed change required, but adds a custom field to manage.

**Solution C -- Match on shared attribute.** Use `email` or `upn` as the common anchor. Both PingFed and the OBO flow include email/UPN. Change the APIM policy to use `preferred_username` instead of `oid`:

```
IDENTITY_CLAIM_NAME = preferred_username
```

And match against the SF User's `Email` or `Username` field. Risk: email is mutable and less stable than `oid`.

### Why OBO Bypasses SSO

The three-phase OBO exchange in APIM does **not** use SSO federation at all. The JWT Bearer flow requires `sub` = Salesforce Username (not FederationIdentifier). The `FederationIdentifier` is only used as a SOQL lookup key to find the Username:

```
Azure AD oid --[SOQL lookup]--> SF Username --[JWT Bearer sub]--> SF Token
```

| Step | SSO (browser login) | OBO (this project) |
|------|--------------------|--------------------|
| Who issues the token? | PingFed (after delegating to Entra) | Entra ID directly (MSAL in chat app) |
| What claim identifies the user? | PingFed's `NameID` | Entra `oid` |
| What does SF match against? | `FederationIdentifier` | `FederationIdentifier` (via APIM SOQL) |
| How does the user get a SF session? | SAML assertion | JWT Bearer (`sub` = Username) |

This means:

1. **SSO can use any IdP chain** (PingFed, Okta, direct) with any claim mapping
2. **OBO is independent** -- it only needs `FederationIdentifier` (or a custom field) set to the Entra ID `oid`
3. **No conflict** if Solution A or B is used -- SSO and OBO coexist as long as the lookup field is consistent

---

## Certificate Rotation

The X.509 certificate used for JWT Bearer signing has a default validity of 365 days. Plan for rotation before expiry.

### When to Rotate

- **Scheduled:** Before the cert expires (check `openssl x509 -in certs/sf-jwt-bearer.crt -noout -enddate`).
- **Unscheduled:** If the private key is compromised or the cert is revoked.

### Rotation Steps (Zero-Downtime)

1. **Generate a new certificate** (same commands as [Phase 1](installation.md#phase-1-generate-x509-certificate)):
   ```bash
   openssl genrsa -out certs/sf-jwt-bearer-new.key 2048
   openssl req -new -x509 -key certs/sf-jwt-bearer-new.key \
     -out certs/sf-jwt-bearer-new.crt -days 365 \
     -subj "/CN=SalesforceJWTBearer"
   openssl pkcs12 -export -out certs/sf-jwt-bearer-new.pfx \
     -inkey certs/sf-jwt-bearer-new.key -in certs/sf-jwt-bearer-new.crt \
     -passout pass:
   ```

2. **Upload new cert to Salesforce Connected App.** Go to Setup > App Manager > your Connected App > Edit. Under "Use digital signatures," upload `sf-jwt-bearer-new.crt`. Salesforce accepts multiple certificates — the old one still works until you remove it.

3. **Upload new PFX to Azure Key Vault.** The postprovision hook handles this, but you can also do it manually:
   ```bash
   az keyvault certificate import --vault-name <kv-name> \
     --name sf-jwt-bearer --file certs/sf-jwt-bearer-new.pfx
   ```
   Key Vault versions the certificate. The new version is active immediately.

4. **Update APIM certificate thumbprint.** The new cert has a different thumbprint:
   ```bash
   # Get new thumbprint
   openssl x509 -in certs/sf-jwt-bearer-new.crt -noout -fingerprint -sha1 \
     | sed 's/://g' | cut -d= -f2

   # Update azd env and APIM Named Value
   azd env set SF_JWT_BEARER_CERT_THUMBPRINT "<new-thumbprint>"
   azd up   # or re-run postprovision hook
   ```

5. **Verify.** Send a message through the Chat App. Check Salesforce Login History for a successful "Connected App" login. If it works, remove the old cert from the Salesforce Connected App.

6. **Replace local files:**
   ```bash
   mv certs/sf-jwt-bearer-new.key certs/sf-jwt-bearer.key
   mv certs/sf-jwt-bearer-new.crt certs/sf-jwt-bearer.crt
   mv certs/sf-jwt-bearer-new.pfx certs/sf-jwt-bearer.pfx
   ```

### Rollback

If the new cert doesn't work, the old cert is still in Salesforce and the previous Key Vault version can be restored:
```bash
az keyvault certificate list-versions --vault-name <kv-name> --name sf-jwt-bearer
```

---

## Current Scope and Limitations

This project is a proof of concept. Before using in production, consider:

- **Destructive operations**: There are no confirmation prompts or audit logs on `write_record` delete operations. Add guardrails appropriate to your org's governance requirements.
- **Token expiry mid-workflow**: APIM caches tokens for 30 minutes and auto-evicts on 401. Long-running workflows may need to retry.
- **Certificate rotation**: The Key Vault certificate used for JWT Bearer signing has a default expiry of 365 days. Plan for rotation.
- **Azure-specific infrastructure**: The deployment stack (APIM, AI Foundry, Container Apps) is Azure-native. Adapting this pattern to other clouds or self-hosted models requires replacing the infrastructure layer, though the [IdP flexibility](#idp-flexibility) section shows the authentication layer is modular.
- **Rate limits**: The Salesforce REST API has per-org API call limits. High-frequency agentic workflows should account for this.

---

## Diagram Sources

### Message Flow

> [Excalidraw source](diagrams/message-flow-sequence.excalidraw)

```mermaid
sequenceDiagram
    actor User as Browser
    participant Chat as Chat App
    participant AI as AI Foundry
    participant APIM
    participant MCP as MCP Server
    participant SF as Salesforce

    User->>Chat: "Show me my open opps over $100k"
    Chat->>AI: Responses API (MSAL token)

    Note over AI: Discover
    AI->>APIM: list_objects("Opportunity")
    APIM->>MCP: OBO token exchange
    MCP->>SF: describeGlobal()
    SF-->>MCP: object list
    MCP-->>AI: objects found

    Note over AI: Learn
    AI->>APIM: describe_object("Opportunity")
    APIM->>MCP: cached token
    MCP->>SF: describe("Opportunity")
    SF-->>MCP: field schema
    MCP-->>AI: schema returned

    Note over AI: Query
    AI->>APIM: soql_query(...)
    APIM->>MCP: cached token
    MCP->>SF: SELECT Name, Amount, ...
    SF-->>MCP: user's own data
    MCP-->>AI: query results

    AI-->>Chat: streamed response
    Chat-->>User: "Here are your open opps..."
```

### OBO Token Exchange (detailed)

> [Excalidraw source](diagrams/obo-token-exchange.excalidraw)

```mermaid
sequenceDiagram
    participant F as AI Foundry
    participant A as APIM
    participant C as Cache
    participant KV as Key Vault
    participant SF as Salesforce
    participant MCP as MCP Server

    F->>A: tool call + Azure AD JWT

    rect rgb(255, 243, 191)
    Note over A: Phase 0 — Validate JWT
    A->>A: check iss, aud, extract oid
    end

    rect rgb(255, 243, 191)
    Note over A,SF: Phase 1 — Resolve SF Username
    A->>C: lookup sf-username-{oid}
    C-->>A: MISS
    A->>KV: sign JWT (sub=svc account)
    A->>SF: JWT Bearer grant
    SF-->>A: service token
    A->>SF: SOQL: Username WHERE FedId = oid
    SF-->>A: username
    A->>C: store sf-username-{oid} (1h TTL)
    end

    rect rgb(255, 243, 191)
    Note over A,SF: Phase 2 — Get User SF Token
    A->>C: lookup sf-token-{username}
    C-->>A: MISS
    A->>KV: sign JWT (sub=user)
    A->>SF: JWT Bearer grant
    SF-->>A: user access token
    A->>C: store sf-token-{user} (30m TTL)
    end

    rect rgb(211, 249, 216)
    Note over A,MCP: Phase 3 — Forward
    A->>A: replace Authorization header
    A->>MCP: request with user's SF token
    end
```

### Chained Federation SSO Flow

> [Excalidraw source](diagrams/chained-federation-sso.excalidraw)

```mermaid
sequenceDiagram
    actor User as Browser
    participant Entra as Entra ID
    participant Ping as PingFederate
    participant SF as Salesforce

    User->>Entra: authenticate (MSAL / redirect)
    Entra-->>User: Entra token (oid, upn)

    Note over User,Ping: SSO redirect chain
    User->>Ping: SAML AuthnRequest
    Ping->>Entra: validate token / fetch claims
    Entra-->>Ping: oid="d178a230...", upn="user@corp.com"

    rect rgb(255, 243, 191)
    Note over Ping: Claim Transformation
    Ping->>Ping: map oid → employee ID "EMP-12345"
    end

    Ping-->>User: SAML Response (NameID="EMP-12345")
    User->>SF: SAML assertion
    SF->>SF: match NameID → FederationIdentifier
    SF-->>User: authenticated session
```

### Multi-IdP Identity Mapping

> [Excalidraw source](diagrams/multi-idp-identity-mapping.excalidraw)

```mermaid
graph LR
    subgraph "SSO Path (browser)"
        A[Entra ID<br/>oid: d178a230] -->|SAML| B[PingFederate]
        B -->|NameID: EMP-12345| C[Salesforce<br/>FederationIdentifier]
    end

    subgraph "OBO Path (API)"
        D[Entra ID<br/>oid: d178a230] -->|oid claim| E[APIM]
        E -->|SOQL: WHERE FedId = oid| F[SF Username]
        F -->|JWT Bearer sub| G[SF Token]
    end

    C -.->|"must match<br/>PingFed NameID"| H{FederationIdentifier<br/>= EMP-12345}
    E -.->|"must match<br/>Entra oid"| I{FederationIdentifier<br/>= d178a230}

    H -.- J["Conflict! Single field,<br/>two different values"]
    I -.- J

    style J fill:#ffc9c9,stroke:#ef4444,color:#1e1e1e
    style B fill:#fff3bf,stroke:#f59e0b,color:#1e1e1e
    style E fill:#ffd8a8,stroke:#f59e0b,color:#1e1e1e
```
