# Installation Guide

Complete walkthrough for deploying the Salesforce Meta-Tool with Identity Propagation from a clean Azure subscription and Salesforce org.

---

## Phase 0: Prerequisites

### Tools

| Tool | Version | Install |
|------|---------|---------|
| Azure Developer CLI | 1.5+ | [Install azd](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) |
| Azure CLI | 2.60+ | [Install az](https://learn.microsoft.com/cli/azure/install-azure-cli) |
| Salesforce CLI | sf 2.x | [Install sf](https://developer.salesforce.com/tools/salesforcecli) |
| Docker Desktop | - | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Python | 3.11+ | [python.org](https://www.python.org/) |
| OpenSSL | - | Pre-installed on macOS/Linux; [Git for Windows](https://gitforwindows.org/) includes it |

### Accounts

- **Azure subscription** with Contributor + User Access Administrator roles
- **Salesforce Developer Edition** — [Sign up free](https://developer.salesforce.com/signup)

### Clone and install

```bash
git clone https://github.com/ozgurkarahan/salesforce-meta-tool-identity-propagation.git
cd salesforce-meta-tool-identity-propagation
pip install -r requirements.txt
```

---

## Phase 1: Generate X.509 Certificate

The certificate is the shared trust anchor between Salesforce and Azure. Salesforce verifies JWT Bearer assertions signed with the private key; Azure Key Vault stores the PFX for APIM to use at runtime.

```bash
mkdir -p certs

# Generate 2048-bit RSA private key
openssl genrsa -out certs/sf-jwt-bearer.key 2048

# Create self-signed certificate (valid 365 days)
openssl req -new -x509 -key certs/sf-jwt-bearer.key \
  -out certs/sf-jwt-bearer.crt -days 365 \
  -subj "/CN=SalesforceJWTBearer"

# Bundle into PFX (no password — Key Vault handles encryption at rest)
openssl pkcs12 -export -out certs/sf-jwt-bearer.pfx \
  -inkey certs/sf-jwt-bearer.key -in certs/sf-jwt-bearer.crt \
  -passout pass:
```

You should now have three files in `certs/`:

| File | Used by |
|------|---------|
| `sf-jwt-bearer.key` | Private key (never leaves your machine until KV upload) |
| `sf-jwt-bearer.crt` | Public cert — uploaded to Salesforce Connected App |
| `sf-jwt-bearer.pfx` | Key + cert bundle — uploaded to Azure Key Vault by postprovision hook |

---

## Phase 2: Salesforce Org Setup

### Sign in to your Salesforce org

If you don't have one yet, sign up at [developer.salesforce.com/signup](https://developer.salesforce.com/signup).

```bash
sf org login web --alias myorg
```

### Run the setup script

```bash
python scripts/setup-sf-org.py --org myorg --email you@example.com \
  --cert certs/sf-jwt-bearer.crt --skip fedid
```

> We skip `fedid` (Federation IDs) for now — it requires Azure resources to exist first. We'll run it in [Phase 4](#phase-4-map-user-identities).

The script runs 4 of 5 SF Setup Steps (step 5, `fedid`, is deferred to [Phase 4](#phase-4-map-user-identities)):

| SF Setup Step | Flag | What it does |
|---------------|------|--------------|
| 1. Connected App | `eca` | Deploys a Connected App with JWT Bearer flow + X.509 certificate. Sets OAuth policies to "Admin approved users are pre-authorized" and assigns profiles. |
| 2. SSO Federation | `sso` | Creates an Entra App Registration and deploys a Salesforce Auth Provider for SSO (interactive browser login). |
| 3. Demo Data | `demo` | Creates a "Standard User - No Delete" profile, a demo user, and sample Account/Opportunity data. |
| 4. Service Account | `svcacct` | Creates a dedicated service account with `Minimum Access - Salesforce` profile and `MCP_OBO_Service_Account` Permission Set (API Enabled + View All Users). |

### Note down these values

The script prints the values you need. You can also retrieve them later:

```bash
# Instance URL
sf org display --target-org myorg --json | python -c "import sys,json; print(json.load(sys.stdin)['result']['instanceUrl'])"
```

| Variable | Where to find it |
|----------|-----------------|
| `SF_INSTANCE_URL` | Script output or `sf org display` — e.g., `https://myorg.my.salesforce.com` |
| `SF_CONNECTED_APP_CLIENT_ID` | Script output (Consumer Key) |
| `SF_SERVICE_ACCOUNT_USERNAME` | Script output — e.g., `mcp.obo.svc@myorg.my.salesforce.com` |

### Optional: Enable SSO login

If you ran the `sso` step, you can enable the "Azure AD" button on your Salesforce login page:

1. Go to **Setup > My Domain > Authentication Configuration**
2. Check the box for the Auth Provider created by the script (e.g., "Azure_AD")
3. Save

This is optional — the OBO flow works without it. SSO login lets end users sign in to Salesforce directly with their Azure AD credentials.

---

## Phase 3: Deploy to Azure

### Authenticate

```bash
az login
azd auth login
```

### Create environment and set variables

```bash
azd env new obo

azd env set SF_INSTANCE_URL "https://your-org.my.salesforce.com"
azd env set SF_CONNECTED_APP_CLIENT_ID "<consumer-key-from-phase-2>"
azd env set SF_SERVICE_ACCOUNT_USERNAME "<svc-username-from-phase-2>"
```

> You do **not** need to set `SF_JWT_BEARER_CERT_THUMBPRINT`. The postprovision hook reads it from Key Vault after uploading the cert.

### Deploy

```bash
azd up
```

This is a single-pass deployment. Here's what happens:

1. **Bicep provisions all Azure resources** (~12 min): Resource Group, Key Vault, APIM, Container Apps (chat + MCP server), AI Foundry project, Container Registry, monitoring. The APIM certificate module is skipped on first deploy (no cert in KV yet).

2. **Container images are built and pushed** (~3 min): Chat App and Salesforce MCP server.

3. **Postprovision hook runs** (~2 min) — these are **Post-Deploy Steps** (separate from the SF Setup Steps above):
   - **Post-Deploy 0:** Uploads `certs/sf-jwt-bearer.pfx` to Key Vault, creates the APIM certificate binding, and sets `SF_JWT_BEARER_CERT_THUMBPRINT` in the azd environment.
   - **Post-Deploy 1:** Creates Chat App Entra app registration (SPA with MSAL.js redirect URIs).
   - **Post-Deploy 2:** Creates the Foundry agent with Salesforce MCP tool configuration.
   - **Post-Deploy 3:** Updates Chat App Container App with Entra client ID and tenant ID.
   - **Post-Deploy 4:** Recreates OBO connection via ARM REST and updates APIM Named Values.

### What to expect

The hook prints progress for each step. Key outputs to look for:

```
--- Step 0: Certificate upload + APIM binding ---
  Certificate imported to Key Vault
  azd env set SF_JWT_BEARER_CERT_THUMBPRINT=A1B2C3...
  APIM certificate binding created
  SfJwtBearerCertThumbprint = A1B2C3...
...
=== Post-provision hook complete ===
```

The Chat App URL is printed at the very end of deployment.

---

## Phase 4: Map User Identities

This step sets each Salesforce user's `FederationIdentifier` to their Azure AD `oid`, enabling the OBO flow to map Azure AD users to Salesforce users.

### Preview changes first

```bash
python scripts/setup-sf-org.py --org myorg --email you@example.com \
  --only fedid --dry-run
```

This shows which users would be updated without making changes.

### Apply

```bash
python scripts/setup-sf-org.py --org myorg --email you@example.com \
  --only fedid
```

### Managed tenant workaround

In managed tenants, user UPNs may not match email addresses (e.g., `user_company.com#EXT#@tenant.onmicrosoft.com`). The script handles this by matching on email address first, then falling back to UPN.

### Who needs Federation IDs?

Only users who will use the Chat App need their `FederationIdentifier` set. The service account does **not** need one — it authenticates via JWT Bearer, not OBO.

---

## Phase 5: Verify

1. **Open the Chat App** at the URL printed after `azd up`
2. **Sign in** with your Azure AD account
3. **Send a message:** *"Show me my Salesforce accounts"*
4. The agent should discover the Account object, query it, and return results
5. **Check Salesforce Login History** (Setup > Login History) — you should see a login from your user via "Connected App" with the OBO Connected App name

If the agent responds without calling tools, check the Foundry connection target URL. If you get a 403 "User Not Mapped" error, re-run [Phase 4](#phase-4-map-user-identities).

### Verify Permission Enforcement

The identity propagation claim is only valuable if Salesforce actually enforces per-user permissions. Test this by comparing results between two users with different access levels.

**Test 1: Field-Level Security (FLS)**

If a user's profile does not grant read access to a field, that field is silently omitted from query results (no error — just missing data):

```
User: "Show me the Amount field on my opportunities"
Agent: soql_query("SELECT Id, Name, Amount FROM Opportunity LIMIT 5")
```

- **Admin user:** Sees `Id`, `Name`, `Amount` in results.
- **Restricted user (no Amount access):** Sees `Id`, `Name` only — `Amount` is silently excluded.
- **If the field is completely inaccessible:** Returns `INVALID_FIELD` error with `errorCode: "INVALID_FIELD"`.

**Test 2: Sharing Rules (Record Visibility)**

Users only see records they own or that are shared with them via sharing rules, roles, or teams:

```
User: "How many accounts do I have?"
Agent: soql_query("SELECT COUNT() FROM Account")
```

- **Admin:** Returns total org count.
- **Sales rep:** Returns only their accounts (owned or shared).

**Test 3: CRUD Permissions**

If a user's profile doesn't allow delete on an object:

```
Agent: write_record(object_name="Account", operation="delete", record_id="001...")
```

Returns: `{"success": false, "errorCode": "INSUFFICIENT_ACCESS", "message": "You do not have permission to delete this record."}`

**Test 4: Confirm in Login History**

Go to Salesforce **Setup > Login History**. Each API call should appear under the user's own identity (not the service account), with the Connected App name as the application.

---

## Approval Flow for Destructive Operations

When the Foundry agent calls an MCP tool, it can be configured to require user approval before execution. This is especially important for write and delete operations.

### How It Works

1. The agent decides to call a tool (e.g., `write_record` with `operation: "delete"`).
2. Foundry checks the MCP connection's approval settings.
3. If approval is required, the Chat App receives an `mcp_approval_request` instead of the tool result.
4. The user sees the tool name, arguments, and can approve or deny.
5. On approval, the Chat App sends an `mcp_approval_response` and the agent continues.

### Configuring Approval in Foundry

Approval settings are configured on the **MCP connection** in AI Foundry, not in the MCP server itself:

1. Go to **AI Foundry > Your Project > Connections**
2. Find the `salesforce-obo` connection
3. Under **Tool approval**, choose one of:
   - **Always require approval** — Every tool call needs user consent (safest for production)
   - **Never require approval** — Tools execute immediately (fastest for development)
   - **Require approval for specific tools** — Granular per-tool control (recommended):
     - `write_record` — Approve (creates, updates, deletes)
     - `process_approval` — Approve (approval workflow actions)
     - `list_objects`, `describe_object`, `soql_query`, `search_records` — No approval needed (read-only)

### Audit Trail

Tool invocations are logged in the Chat App at INFO level with:
- `tool_call`: tool name, arguments (truncated), errors
- `tool_approval_requested`: tool name, server label, arguments

These logs flow to App Insights when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set.

---

## Quick Reference: Values to Note Down

| Phase | Variable | Source |
|-------|----------|--------|
| 2 | `SF_INSTANCE_URL` | `sf org display` — instance URL |
| 2 | `SF_CONNECTED_APP_CLIENT_ID` | `setup-sf-org.py` output — Consumer Key |
| 2 | `SF_SERVICE_ACCOUNT_USERNAME` | `setup-sf-org.py` output — service account username |

All three are set via `azd env set` before `azd up`. The certificate thumbprint is handled automatically.

---

## Common Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| `azd up` fails at cert module | First deploy, cert not in KV yet | Fixed by conditional Bicep module — should not happen. If it does, re-run `azd up`. |
| "No local cert found" in postprovision | `certs/sf-jwt-bearer.pfx` missing | Generate cert per [Phase 1](#phase-1-generate-x509-certificate) and re-run `azd up`. |
| RBAC propagation timeout | Role assignment takes >60s | Re-run `azd up` — the cert import retries with backoff. |
| "Project not found" during agent creation | Foundry data plane propagation (5-15 min) | The hook retries 6 times. If it still fails, re-run: `python hooks/postprovision.py` |
| "Project not found" after `azd down` | Soft-deleted Cognitive account name conflict | Increment `COGNITIVE_ACCOUNT_SUFFIX`: `azd env set COGNITIVE_ACCOUNT_SUFFIX 2` |
| 401 "Invalid Azure AD token" | Token issuer/audience mismatch | Verify `validate-jwt` in APIM policy includes both v1 and v2 issuers |
| 502 "SF Service Token Failed" | Bad cert, wrong client ID, or service account not pre-authorized | Check cert thumbprint, Connected App consumer key, and service account Permission Set |
| 403 "User Not Mapped" | No SF user with matching `FederationIdentifier` | Run `setup-sf-org.py --only fedid` |
| 502 "SF Token Exchange Failed" | Target SF user not pre-authorized for Connected App | Assign user's profile to the Connected App via `setup-sf-org.py --only eca` |
| APIM breaks MCP streaming | Response body logging enabled in APIM diagnostics | Set response body bytes to `0` in APIM diagnostics (All APIs scope) |
| Agent responds without tools | Foundry connection misconfigured | Check connection target URL matches APIM OBO endpoint |
