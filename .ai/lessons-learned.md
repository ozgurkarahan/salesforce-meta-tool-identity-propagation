# Lessons Learned

Project-specific debugging history and corrections. Update after every mistake or discovery.
Workflow rules live in `~/.ai/workflow.md` (global) — do NOT duplicate them here.
Stable patterns graduated to `~/.claude/knowledge/` — see azure-apim.md, salesforce.md, etc.

---

## Project-Specific Lessons

### 2026-03-29 — SAML SSO: 10 gotchas in one session

**Context:** Rewrote `step_sso` from OIDC (required Apex RegistrationHandler) to SAML (pure config). Hit 10 distinct issues before end-to-end SSO worked.

**Key lessons (all graduated to `~/.claude/knowledge/salesforce.md`):**
1. SF SamlSsoConfig metadata: lowercase extension, needs `<name>`, `RSA-SHA256` not `RSA_SHA256`, `requestSigningCertId` = 15-char record ID
2. Must deploy a self-signed Certificate FIRST (via mdapi), then reference its ID
3. Entra: managed tenant blocks custom identifier URIs via `az ad app update` but Graph API PATCH allows it
4. Entra: `addTokenSigningCertificate` creates NEW cert each call — must set `preferredTokenSigningKeyThumbprint` and match cert to SF
5. SF sends bare org URL as SAML ACS reply URL, not the `/services/auth/sso/<name>` path
6. Logout URL must be `/oauth2/logout` not `/saml2`

**Rule:** When implementing SAML SSO, follow the exact sequence in `project_sso_saml.md` memory. The order matters (cert before config, preferred key before federation metadata download).

### ApiHub PKCE vs Salesforce OAuth — Re-authenticate flow

**Problem (original):** After `azd up`, the Salesforce OAuth connection has no user token. ApiHub registers the connector with `identityProvider: oauth2pkce` (read-only) and sends `code_challenge` to Salesforce. Previously, this caused "Invalid Code Verifier" errors during the ApiHub consent flow.

**Update (2026-02-25):** The native ApiHub PKCE consent flow now completes successfully after a clean DELETE+PUT (tested on `rg-sf-orders-idp` deployment). Either the platform bug was fixed or DELETE+PUT resets the PKCE state that caused the mismatch.

**Key requirement — postprovision DELETE+PUT:**
- Bicep-created connections do NOT register the ApiHub connector that Foundry needs for interactive OAuth consent
- The postprovision hook (`update_sf_oauth_connection()`) DELETEs the Bicep connection and PUTs a fresh one via ARM REST, which triggers ApiHub setup
- This matches the `secu-propagate-identity` pattern where the native consent flow was confirmed working

**Primary runtime mechanism — Re-authenticate button:**
1. SF tokens expire after 2h; the chat app detects auth errors and shows a "Re-authenticate" button
2. `POST /api/reset-mcp-auth` DELETEs the existing connection (clearing the expired refresh token), then PUTs a fresh one without credentials
3. The next agent call triggers `oauth_consent_request` → user completes native ApiHub consent → fresh tokens stored

**Optional fallback — `grant-sf-mcp-consent.py`:**
- Bypasses ApiHub entirely: runs a direct OAuth auth code flow to SF (no PKCE) via `localhost:8444`, then DELETE+PUTs the connection with the refresh token baked in
- Useful if the native ApiHub consent flow fails, or for headless/automated setups where browser consent is impractical

**Rule:** After `azd up`, the postprovision hook DELETE+PUTs the connection to register the ApiHub connector. The first agent call triggers `oauth_consent_request` — complete the native consent flow in the browser. `grant-sf-mcp-consent.py` is an optional fallback for headless/automated setups. The re-authenticate button handles token expiry at runtime.

### 2026-02-25 — Missing auto-retry after consent chain
**Mistake:** `handleResponse()` in `meta-tool-salesforce` was missing the `awaitingPostConsentRetry` branch that auto-retries the original query after consent completes. This caused the agent to show text responses without ever calling MCP tools — making it look like the PKCE consent was broken when in fact the tokens were stored but never used.
**Root cause:** Code was extracted from `secu-propagate-identity` but this branch was accidentally dropped.
**Rule:** When extracting code between projects, diff the critical UI flow functions (handleResponse, resetAndRetry) to ensure no branches are missing. The missing auto-retry was the real cause of the "PKCE doesn't work" misdiagnosis.

### 2026-02-26 — sf CLI flag names differ across versions
**Mistake:** Used `--target-dir` for `sf project retrieve start` — the correct flag is `--output-dir`. The script failed immediately.
**Root cause:** Relied on plan/memory for flag names instead of checking `sf <command> --help` on the target machine.
**Rule:** Always run `sf <command> --help` to verify exact flag names before writing sf CLI automation. Flag names change between sf CLI versions.

### 2026-02-26 — sf CLI requires sfdx-project.json + force-app directory
**Mistake:** `sf project retrieve start` and `sf project deploy start` require a valid SFDX project structure (sfdx-project.json + the packageDirectory path must exist). Running from a bare temp directory failed with `InvalidProjectWorkspaceError` then `MissingPackageDirectoryError`.
**Root cause:** Assumed sf CLI would create the directory structure on retrieve. It doesn't — it validates the project workspace first.
**Rule:** When using sf CLI in temp directories, always create a minimal `sfdx-project.json` and `mkdir -p force-app/main/default` before running retrieve/deploy commands. Use `cwd` parameter in subprocess instead of `cd` in the command string.

### 2026-02-26 — Salesforce standard profile metadata names differ from labels
**Mistake:** Tried to retrieve `Profile:Standard User` via Metadata API — Salesforce returned "entity not found". The internal metadata name for "Standard User" is `Standard`, not `Standard User`.
**Root cause:** Salesforce uses internal API names for standard profiles that differ from UI labels (e.g., "System Administrator" = `Admin`, "Standard User" = `Standard`).
**Rule:** Don't try to retrieve and clone standard profiles via Metadata API — generate custom profile XML from scratch instead. This is simpler and avoids the metadata name mismatch problem entirely.

### 2026-02-26 — Custom Salesforce profiles need explicit permissions
**Mistake:** Generated a minimal custom profile with only `objectPermissions` — it was missing `ApiEnabled`, `LightningExperienceUser`, and other `userPermissions`. The demo user couldn't use the API or access Lightning Experience.
**Root cause:** Custom profiles don't inherit user permissions from the license — only object permissions default from the license. System permissions like `ApiEnabled` must be explicitly granted in the profile metadata.
**Rule:** When creating custom Salesforce profiles via Metadata API, always include these `userPermissions`: `ApiEnabled`, `LightningExperienceUser`, `RunReports`, `ExportReport`. Check the Standard User profile's permissions via SOQL (`SELECT Permissions* FROM PermissionSet WHERE Profile.Name='Standard User'`) as a reference.

### 2026-02-26 — Windows cp1252 encoding breaks sf CLI and Unicode output
**Mistake:** `subprocess.run(text=True)` on Windows uses cp1252 by default. sf CLI output containing non-ASCII bytes caused `UnicodeDecodeError`. Arrow characters (`→`) in print statements also failed.
**Root cause:** Windows default encoding is cp1252, not UTF-8. sf CLI outputs UTF-8.
**Rule:** Always pass `encoding="utf-8", errors="replace"` to `subprocess.run()` on Windows. Avoid non-ASCII characters (→, •, etc.) in print statements — use ASCII equivalents (`->`, `-`). SF User Alias field max is 8 characters.

### 2026-02-27 — SF Connected App must require PKCE to match ApiHub
**Mistake:** SF Connected App had PKCE disabled while ApiHub registers with `identityProvider: oauth2pkce` and sends `code_challenge` to Salesforce. SF ignored the `code_challenge`, so the PKCE handshake was never enforced end-to-end. This mismatch likely contributed to token refresh/re-exchange failures after expiry.
**Root cause:** Both sides of the OAuth flow must agree on PKCE. ApiHub always uses PKCE, but SF was not validating it. Without enforcement, the `code_verifier`/`code_challenge` contract is meaningless.
**Rule:** When the OAuth client (ApiHub) uses PKCE, the OAuth server (SF Connected App) must also require PKCE. Enable PKCE manually in SF Setup (cannot be done via Metadata API). Ensure all fallback scripts (`grant-sf-mcp-consent.py`) use PKCE so they don't break when SF requires it.

### 2026-02-27 — ECA Metadata API format differs from documentation
**Mistake:** Generated ECA metadata using speculative directory/file names (`externalClientApplications/`, `.externalClientApplication-meta.xml`, `commaSeparatedOAuth2Scopes`). The actual format from a real SF org is completely different.
**Root cause:** Assumed Metadata API naming conventions without retrieving from a real org. SF's actual SFDX source format for ECAs uses non-obvious names.
**Rule:** Always `sf project retrieve start` from a real org before writing metadata generation code. The actual format is:
- ECA directory: `externalClientApps/` (not `externalClientApplications/`)
- ECA suffix: `.eca-meta.xml` (not `.externalClientApplication-meta.xml`)
- OAuth settings name: `{AppName}_oauth` with suffix `.ecaOauth-meta.xml`
- OAuth settings field: `commaSeparatedOauthScopes` (not `commaSeparatedOAuth2Scopes`)
- OAuth settings must include `externalClientApplication` and `label` fields
- PKCE (`isCodeCredentialFlowWithPKCE`) is NOT a valid metadata field -- SF rejects it with "Element invalid at this location". PKCE is UI-only.
- `ConsumerKey` is NOT a field on the Tooling API `ConnectedApplication` object. ECA-created apps show 0 records in `ConnectedApplication` SOQL queries.

### 2026-02-27 — Post-consent retry loop needed for ApiHub propagation delay
**Bug:** After user completed OAuth consent, `continueAfterConsent()` immediately sent a continuation which got ANOTHER `consent_required` (ApiHub propagation delay). `handleResponse()` checked `consent_required` before `awaitingPostConsentRetry`, so it re-showed the consent banner. User clicked "I've completed" without re-opening the NEW consent link → infinite loop (6+ times) until agent gave up.
**Root cause:** ApiHub takes a few seconds to propagate tokens after consent completion. The chat app had no tolerance for this delay — any `consent_required` after consent showed the banner again.
**Fix:** In `handleResponse()`, when `awaitingPostConsentRetry` is true and we get `consent_required`, silently wait 3 seconds and retry (up to 4 times) instead of re-showing the banner. Only re-show if all poll retries are exhausted.
**Rule:** After OAuth consent completion, always add a poll-and-retry loop before re-showing consent UI. ApiHub needs a few seconds to propagate tokens. The user completing consent once should be enough — the app must absorb the delay.

### 2026-02-27 — ApiHub does NOT auto-refresh OAuth tokens (conclusively proven)
**Finding:** ApiHub never refreshes tokens for RemoteTool/GenericProtocol connections. Proven via diagnostic test with 10-minute SF token TTL.
**Evidence (JWT-level proof):**
- Successful call at 21:08 UTC and failed call at 21:18 UTC sent the **byte-identical JWT** (same `iat`, `exp`, signature)
- JWT claims: `iat=21:05:01`, `exp=21:15:01` (10min TTL). Token expired at 21:15:01.
- At 21:18:52 (3m51s past expiry), ApiHub provided the same expired token to Foundry — no proactive `exp` check
- At 21:20:07 (5m6s past expiry), second call — still the same expired token, no refresh between failures
- SF login history: **zero entries** after consent — no `grant_type=refresh_token` POST to `/services/oauth2/token`
- SF returned `INVALID_JWT_FORMAT` / `INVALID_AUTH_HEADER` (how SF rejects expired JWTs)
**What doesn't work:**
1. Proactive refresh (checking `exp` before providing token) — expired tokens ARE sent
2. Reactive refresh (401 → refresh → retry) — no refresh after receiving 401
3. `refreshUrl` in connection config — accepted but never called
**Diagnostic setup:** Removed APIM `validate-jwt` so requests flow through to SF. MCP server raises `SalesforceAuthError` on 401 (but FastMCP catches exceptions in tool handlers before middleware — see lesson below).
**Rule:** ApiHub's `refreshUrl` is non-functional for RemoteTool connections. Token expiry ALWAYS requires re-authentication. Design the UX accordingly.

### 2026-02-27 — FastMCP catches tool exceptions before ASGI middleware
**Mistake:** Added `SalesforceAuthError` (not a subclass of `httpx.HTTPStatusError`) to bypass MCP tool error handlers and propagate to `BearerTokenMiddleware`. Expected the middleware to catch it and return raw HTTP 401. Instead, FastMCP's internal tool execution wrapper caught it first and returned it as `Error executing tool ...: (401, b'...')` inside an HTTP 200 MCP tool result.
**Root cause:** FastMCP wraps ALL tool handler calls in a try/except that catches any `Exception` and returns it as a tool error string. The exception never reaches the ASGI middleware layer because FastMCP catches it at the MCP protocol level.
**Call chain:** `HTTP request → Middleware → FastMCP router → tool handler → raises SalesforceAuthError → FastMCP catches here (HTTP 200 with error string) → middleware never sees it`
**Rule:** ASGI middleware cannot catch exceptions raised inside MCP tool handlers — FastMCP intercepts them first. To return non-200 HTTP responses from tool-level errors, the tool handler itself must explicitly return an HTTP error response, or a custom MCP transport/router must be used.

### 2026-02-27 — SF `INVALID_JWT_FORMAT` / `INVALID_AUTH_HEADER` for expired JWT tokens
**Finding:** When a Salesforce JWT-format access token (Core Token Encryption) has a valid signature but an expired `exp` claim, SF returns `[{"message":"INVALID_JWT_FORMAT","errorCode":"INVALID_AUTH_HEADER"}]` with HTTP 401. This differs from the traditional `INVALID_SESSION_ID` error returned for opaque session-based tokens.
**Confirmed from Salesforce:** The request went directly to `orgfarm-*.develop.my.salesforce.com` — not intercepted by APIM (validate-jwt removed). Response format `[{errorCode, message}]` with `content-type: application/json;charset=UTF-8` is standard SF REST API error format.
**Context:** SF now issues access tokens as signed JWTs (header `tnk: "core/prod/..."`, alg RS256). SF validates the JWT `exp` claim server-side and returns `INVALID_JWT_FORMAT` when expired — the name is misleading since the format is valid, only the `exp` is past.
**Rule:** `INVALID_JWT_FORMAT` + `INVALID_AUTH_HEADER` from SF = expired JWT access token (not structurally malformed). Check the `exp` claim in the JWT payload to confirm. This is different from `INVALID_SESSION_ID` which applies to older opaque session tokens.

### 2026-02-27 — Don't run interactive scripts from Claude Code Bash tool
**Mistake:** Ran `test-reauth-flow.py` (which uses `input()` for Phase 4) from Claude Code's Bash tool. The script crashed with `EOFError` at the interactive prompt, leaving tokens in a wiped state each time.
**Root cause:** Claude Code's Bash tool runs non-interactively — `stdin` is closed, so `input()` raises `EOFError`.
**Rule:** Never run scripts with `input()` or other interactive stdin from Claude Code. For test scripts with interactive steps, either: (a) split into non-interactive + interactive parts, (b) have the user run from their own terminal, or (c) just do the ARM manipulation directly and let the user test through the UI.

### 2026-03-01 — ECA metadata does not support certificate or callbackUrl fields
**Mistake:** Attempted to deploy an ECA (`ExtlClntAppOauthSettings`) with `certificate` and `callbackUrl` fields for JWT Bearer flow. SF rejected both with "Element invalid at this location in type ExtlClntAppOauthSettings".
**Root cause:** The ECA metadata type (`ExtlClntAppOauthSettings`) has a very limited schema compared to the classic `ConnectedApp` metadata type. Fields like `certificate`, `callbackUrl`, and `isSecretRequired` are NOT valid in ECA OAuth settings metadata. Certificate upload for ECAs is UI-only (same as PKCE).
**Fix:** Use the classic `ConnectedApp` metadata type instead of ECA for JWT Bearer flow. ConnectedApp supports `certificate`, `callbackUrl`, `isAdminApproved`, and `scopes` in the `oauthConfig` element. JWT Bearer works identically with classic Connected Apps.
**Rule:** For JWT Bearer flow requiring certificate upload, always use `ConnectedApp` metadata (not ECA). The valid elements for `ExtlClntAppOauthSettings` are: `commaSeparatedOauthScopes`, `externalClientApplication`, `isFirstPartyAppEnabled`, `label`. Everything else is UI-only. Use `ConnectedApp` when you need `certificate`, `callbackUrl`, `isAdminApproved`, `scopes`, `isConsumerSecretOptional`, etc.

### 2026-03-01 — FederationIdentifier must be unique per org
**Mistake:** Tried to set the same Azure AD `oid` as FederationIdentifier on two different SF users (admin + demo). SF rejected the second with "This Federation ID is already in use."
**Root cause:** FederationIdentifier is unique per org — each SF user must map to a different identity provider user.
**Rule:** Each SF user needs its own Azure AD user identity (different `oid`). Cannot map multiple SF users to the same Azure AD oid.

### 2026-03-01 — Azure AD UPN in managed tenants differs from user email
**Mistake:** Used `az ad user show --id "ozgurkarahan@microsoft.com"` which failed because the user's actual UPN in the managed tenant is `ozgurkarahan@MngEnvMCAP549101.onmicrosoft.com`.
**Root cause:** Managed environments (MCAP) use `.onmicrosoft.com` UPNs, not the user's email domain. The `mail` attribute may match, but `userPrincipalName` does not.
**Rule:** In managed tenants, use `az ad signed-in-user show` to get the current user's oid directly. Don't assume email == UPN. The `set-sf-federation-id.py` script may need manual oid specification for managed tenant users.

### 2026-03-01 — APIM context.Deployment.Certificates is keyed by thumbprint, not name
**Mistake:** Used `context.Deployment.Certificates["sf-jwt-bearer"]` (certificate resource name) in the APIM policy. Got `KeyNotFoundException` at runtime → HTTP 500.
**Root cause:** `context.Deployment.Certificates` is an `IReadOnlyDictionary<string, X509Certificate2>` keyed by **certificate thumbprint** (hex string), not by the APIM certificate resource name.
**Fix:** Store the thumbprint in a Named Value (`SfJwtBearerCertThumbprint`) and use `context.Deployment.Certificates["{{SfJwtBearerCertThumbprint}}"]`.
**Rule:** Always reference APIM certificates by thumbprint in policy expressions. The `authentication-certificate` policy element supports cert names via `certificate-id`, but `context.Deployment.Certificates[...]` does NOT.

### 2026-03-01 — APIM on-error handler must guard against missing variables
**Mistake:** The `on-error` handler accessed `context.Variables["sfUsername"]` which wasn't set when `validate-jwt` failed (inbound processing stopped before that variable was created). The `KeyNotFoundException` turned the 401 into a 500.
**Root cause:** APIM's `on-error` section catches ALL errors, including `validate-jwt` failures. Variables set AFTER the failing policy element are not available.
**Fix:** Added `context.Variables.ContainsKey("sfUsername")` guard to the on-error condition.
**Rule:** In APIM on-error handlers, always use `ContainsKey()` or `GetValueOrDefault()` for variables that may not exist if an earlier policy element fails.

### 2026-03-01 — APIM validate-jwt v1 vs v2 token issuers
**Mistake:** Used v2 OIDC config URL (`/v2.0/.well-known/openid-configuration`) for `validate-jwt`. Tokens from `az CLI` are v1 (issuer: `sts.windows.net`) and were rejected because the v2 OIDC config only lists the v2 issuer.
**Root cause:** Azure AD v1 tokens have `iss: "https://sts.windows.net/{tid}/"`, v2 tokens have `iss: "https://login.microsoftonline.com/{tid}/v2.0"`. The `openid-config` URL determines which issuer is validated.
**Fix:** Added explicit `<issuers>` block accepting both v1 and v2 issuers while keeping the v2 OIDC endpoint for signing key discovery.
**Rule:** When validating Azure AD tokens from multiple sources (CLI, MSAL, Foundry), always include both v1 and v2 issuers in the `<issuers>` element.

### 2026-03-01 — SF JWT Bearer sub claim always requires username, not FederationIdentifier
**Mistake:** Used Azure AD `oid` (FederationIdentifier value) as the JWT Bearer `sub` claim. SF returned `"user hasn't approved this consumer"` even though the Connected App was properly configured with admin pre-authorization and profile assignment.
**Root cause:** JWT Bearer `sub` always resolves against SF `Username` — org-level SSO does not change this. The SSO/FederationIdentifier matching caveat applies only to the SAML Bearer Assertion flow, which is a different grant type entirely.
**Fix:** Added a three-phase approach in the APIM policy: (1) service account JWT Bearer exchange, (2) SOQL query to look up SF username from FederationIdentifier, (3) user-specific JWT Bearer exchange with the SF username.
**Rule:** JWT Bearer `sub` must be the SF username. The APIM policy resolves it from FederationIdentifier via a service token + SOQL lookup. The SOQL result and tokens are cached for performance. SSO configuration does not eliminate this requirement.

### 2026-03-01 — APIM policy Razor/CSHTML requires braces around if-return
**Mistake:** Used `if (condition) return "";` (single-line) in an APIM policy expression block. APIM rejected it with "Expected a \"{\" but found a \"return\"".
**Root cause:** APIM uses Razor/CSHTML syntax for policy expressions. In multi-statement blocks (`@{ ... }`), control flow statements like `if` must use braces around the body. Single-statement control flow is not allowed.
**Fix:** Changed to `if (condition) { return ""; }`.
**Rule:** Always wrap `if`/`else`/`for`/`while` bodies in braces `{ }` inside APIM policy expressions. Even single-statement bodies need explicit braces.

### 2026-03-01 — SF Connected App profiles are assigned via SetupEntityAccess (not metadata)
**Mistake:** Tried to include `<profiles>` and `<permittedUsers>` elements in ConnectedApp metadata. Both are invalid metadata elements — SF rejected them with "Element invalid at this location".
**Root cause:** Connected App profile assignment and "Permitted Users" policy are NOT part of the Salesforce Metadata API. They're managed via the Tooling API (`OptionsAllowAdminApprovedUsersOnly` field) and `SetupEntityAccess` records.
**Fix:** Used `SetupEntityAccess` API with `ParentId` = Profile's PermissionSet ID, `SetupEntityId` = ConnectedApp ID. Used Tooling API query to confirm `OptionsAllowAdminApprovedUsersOnly`.
**Rule:** For Connected App profile pre-authorization: (1) Set `isAdminApproved: true` in oauthConfig metadata, (2) Assign profiles via `SetupEntityAccess` where `ParentId` = the profile's PermissionSet ID (query `PermissionSet WHERE IsOwnedByProfile=true AND Profile.Name='...'`), `SetupEntityId` = ConnectedApp ID. Do NOT include `<profiles>` or `<permittedUsers>` in metadata.

### 2026-03-01 — UserEntraToken connection requires audience property
**Mistake:** Created a `UserEntraToken` Foundry connection without the `audience` property. Foundry returned `"Missing required query parameter: audience"` when trying to fetch the user's token.
**Root cause:** The `audience` property tells Foundry what audience to request in the Azure AD token. Without it, Foundry can't construct the token request. The Bicep `properties` schema doesn't surface `audience` as a required field — it's optional in the API but required for UserEntraToken to function.
**Fix:** Set `audience: 'https://ai.azure.com'` on the connection (matching the APIM policy's `validate-jwt` audience).
**Rule:** For `UserEntraToken` connections, always set the `audience` property. It must match the `<audience>` in the APIM `validate-jwt` policy. Check the connection via ARM GET to confirm `audience` is not null.

### 2026-03-01 — Check logs before diagnosing
**Mistake:** When the user reported "Missing required query parameter: audience", I assumed the fix before checking App Insights logs. The user corrected me: "are you sure? did you check the logs?"
**Root cause:** Jumped to diagnosis without evidence. Confirmation bias — assumed the fix matched the symptom without verifying.
**Rule:** Always check logs (App Insights, APIM trace, SF login history) FIRST, then diagnose. Never propose a fix for a runtime error without checking the actual error in the logs.

### 2026-03-01 — Bicep conditionals don't delete old resources
**Finding:** `if (sfAuthMode == 'obo')` prevents deployment of a resource but does NOT delete it if it was deployed under a previous mode (e.g., `oauth2`). Switching `SF_AUTH_MODE` from `oauth2` to `obo` leaves the `salesforce-oauth` connection orphaned.
**Impact:** The Foundry project had two connections — `salesforce-oauth` (orphan) and `salesforce-obo` (active). The orphan is harmless but confusing.
**Rule:** Bicep conditionals control creation, not deletion. When switching modes, manually delete orphaned resources or add cleanup logic to the postprovision hook.

### 2026-03-01 — JWT Bearer sub always resolves to SF Username, not FederationIdentifier
**Mistake:** Assumed that configuring org-level SSO would allow JWT Bearer `sub` to match against `FederationIdentifier` instead of `Username`, potentially eliminating the three-phase APIM approach.
**Root cause:** Confused two different OAuth grant types. The SSO/FederationIdentifier matching caveat applies only to the **SAML Bearer Assertion** flow (`urn:ietf:params:oauth:grant-type:saml2-bearer`), not to the **JWT Bearer** flow (`urn:ietf:params:oauth:grant-type:jwt-bearer`). JWT Bearer always resolves `sub` against SF `Username` regardless of SSO configuration.
**Rule:** JWT Bearer `sub` always matches SF `Username` — SSO does not change this. Don't confuse JWT Bearer with SAML Bearer Assertion. SAML Bearer Assertion could use FederationIdentifier but requires XML signature construction (no native APIM support), making it impractical. The three-phase JWT approach (service token → SOQL lookup → user token) is the correct architecture.

### 2026-03-01 — Azure RBAC for AI Foundry must be scoped to the project, not just the account
**Mistake:** Assigned `Cognitive Services OpenAI Contributor` and `Azure AI Developer` roles at the Cognitive Services **account** scope (`Microsoft.CognitiveServices/accounts/aoai-sf-mcp-obo`). The chat app still got `PermissionDenied` — the required data action `Microsoft.CognitiveServices/accounts/AIServices/agents/write` was not recognized at that scope.
**Root cause:** The chat app calls the AI Foundry **project-scoped** endpoint (`/api/projects/aiproj-sf-mcp-obo/openai/*`). Azure RBAC for AI Foundry projects requires role assignments at the **project** scope (`Microsoft.CognitiveServices/accounts/.../projects/aiproj-sf-mcp-obo`), not just the parent account. The `az role assignment create` CLI command also failed with `MissingSubscription` on this resource type — had to use `az rest --method PUT` against the ARM role assignments API directly.
**Fix:** Assigned `Azure AI Developer` at the project scope via REST API:
```
PUT /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/accounts/{account}/projects/{project}/providers/Microsoft.Authorization/roleAssignments/{guid}?api-version=2022-04-01
```
**Rule:** For AI Foundry projects, always assign RBAC roles at the **project** scope, not the parent Cognitive Services account. Use `az rest` (not `az role assignment create`) for project-scoped assignments — the CLI command has a known bug with nested resource scopes. The project resource ID can be discovered with API version `2025-06-01` or later.

### 2026-03-02 — "Project not found" is API version mismatch, not audience bug
**Mistake:** Assumed the `AIProjectClient` SDK was using the wrong token audience because curl with `ai.azure.com` token worked but the SDK failed. The audience was actually correct (`ai.azure.com/.default` in both).
**Root cause:** The SDK (`azure-ai-projects` v2.0.0b3) uses `api-version=2025-11-15-preview` (new Foundry Project API), while the curl test used `2025-05-15-preview` (classic Agent Service API). These are **different backend services** behind the same hostname with independent propagation timelines. After a fresh deploy, the classic Agent Service data plane propagates first; the new Foundry Project data plane (`2025-11-15-preview`) takes longer. That's why curl succeeded while the SDK failed at the same time.
**Key details:**
- SDK API version: `_configuration.py` line 40 → `api_version = "2025-11-15-preview"`
- Classic agents API: `2025-05-15-preview` — propagates faster after fresh deploy
- New Foundry Project API: `2025-11-15-preview` — separate, slower propagation
- `create_version()` only exists in the new API; classic API uses `create()`
**Rule:** After fresh deploy, retry `create_agent()` with backoff (postprovision.py now does this). When debugging "endpoint works in curl but not SDK", always compare the `api-version` parameter — different versions can route to entirely different backend services. Check SDK defaults in `_configuration.py`.

### 2026-03-02 — Fresh deploy requires cert upload before Bicep can complete
**Mistake:** Ran `azd up` after `azd down --purge` without uploading the Key Vault certificate first. Bicep references `sf-jwt-bearer` cert in a Key Vault secret reference, so provisioning failed with "A secret with (name/id) sf-jwt-bearer was not found in this key vault."
**Root cause:** `--purge` deletes Key Vault and all its contents. The Bicep APIM module references the cert for JWT signing. On first deploy after purge, the cert doesn't exist yet.
**Fix (original):** After Key Vault is created (partial `azd up`), upload the PFX cert (`az keyvault certificate import`), then re-run `azd up`. Also need to assign Key Vault Certificates Officer role via `az rest` (RBAC not set up yet since Bicep failed partway).
**Fix (automated — 2026-03-03):** Two changes eliminate the two-pass deploy: (1) Bicep cert module is now conditional on `!empty(sfJwtBearerCertThumbprint)` — skips on first deploy. (2) Postprovision hook Step 0 (`upload_cert_and_configure_apim()`) auto-uploads `certs/sf-jwt-bearer.pfx` to KV, assigns deployer RBAC with retry, creates APIM cert binding via ARM REST, and sets `SF_JWT_BEARER_CERT_THUMBPRINT` in azd env. Single `azd up` now works from scratch.
**Rule:** Keep `certs/sf-jwt-bearer.pfx` in the repo root. `azd up` handles everything in a single pass. If cert is missing, the hook skips with a message pointing to `docs/installation.md`.

### 2026-03-03 — Azure AI User is least-privilege role for Foundry agents, not Azure AI Developer
**Mistake:** Originally assigned `Azure AI Developer` (`64702f94-...`) for users to access the Foundry agent API. This role is over-privileged — it includes OpenAI, SpeechServices, ContentSafety, and MaaS data actions that are not needed.
**Root cause:** On Mar 1 when fixing the initial PermissionDenied error, grabbed `Azure AI Developer` without checking if a least-privilege alternative existed. Microsoft's Foundry RBAC docs explicitly recommend `Azure AI User` as the minimum role.
**Fix:** Switched to `Azure AI User` (`53ca6127-db72-4b80-b1b0-d745d6d5456d`) at the project scope. Confirmed it works — the role includes `Microsoft.CognitiveServices/accounts/AIServices/agents/write` which is the only data action `responses.create()` needs.
**Security team justification:** The chat app passes the user's own Azure AD token to Foundry (identity propagation pattern). Foundry checks RBAC on the user's identity. Azure AI User is least-privilege: read + data actions only, no control-plane permissions. Must be at project scope (not account) for least-privilege scoping.
**Rule:** For Foundry agent access, always use `Azure AI User` (`53ca6127-db72-4b80-b1b0-d745d6d5456d`), not `Azure AI Developer`. Azure AI Developer is only needed when users also need SpeechServices/ContentSafety/MaaS access. Graduated to `~/.claude/knowledge/azure-identity.md` and `~/.claude/knowledge/foundry-sdk.md`.

### 2026-03-03 — Update to Mar 1 lesson: correct role for Foundry project RBAC
**Update:** The Mar 1 lesson "Azure RBAC for AI Foundry must be scoped to the project" recommended `Azure AI Developer`. The correct least-privilege role is `Azure AI User`. Updated the cross-project knowledge files accordingly.

### 2026-03-04 — httpx.AsyncClient lifecycle in ASGI lifespan with module-level singletons
**Mistake:** After `azd up` (Container App revision update), all MCP tool calls returned "Cannot send a request, as the client has been closed." The chat app showed "there's an issue accessing the account details."
**Root cause:** The `SalesforceClient` is a module-level singleton. ASGI shutdown calls `sf.close()` which closes the `httpx.AsyncClient`. On restart (new revision), Python's module cache retains the singleton with the closed client — `__init__` doesn't re-run.
**Fix:** Recreate the httpx client at the start of each lifespan cycle: `sf._client = httpx.AsyncClient(timeout=30.0)` in the `lifespan()` async context manager.
**Rule:** For module-level singletons holding `httpx.AsyncClient`, always recreate the client in the ASGI lifespan startup phase. Don't rely on `__init__` running again — the module singleton persists across Container App revision restarts.

### 2026-03-04 — APIM rate-limit-by-key increment-condition breaks with SSE streaming
**Mistake:** Added `increment-condition="@(context.Response.StatusCode != 429)"` to `rate-limit-by-key`. Rate limiting never triggered — 65 requests all passed.
**Root cause:** `increment-condition` defers counter incrementing to the outbound pipeline, which doesn't fire properly for SSE streaming responses (`forward-request buffer-response="false"`). The counter never increments.
**Fix:** Removed `increment-condition` attribute entirely. Rate limiting then worked correctly.
**Rule:** Don't use `increment-condition` on `rate-limit-by-key` when the backend uses SSE streaming (`buffer-response="false"`). The outbound pipeline behavior is unreliable for streamed responses.

### 2026-03-04 — APIM ClientConnectionFailure on GET SSE is cosmetic for MCP
**Finding:** Every GET SSE request through APIM shows `ClientConnectionFailure: Client connection was unexpectedly closed` with status 0. This does NOT affect MCP tool call functionality.
**Root cause:** Foundry's MCP client creates a new session per tool call (init → tool call → delete). It opens a GET SSE listener but the MCP server returns tool results inline via POST (200), not via the GET SSE stream. The GET stream times out or is closed by the client, which APIM logs as `ClientConnectionFailure`.
**Rule:** `ClientConnectionFailure` on GET SSE requests in APIM is cosmetic for MCP Streamable HTTP when the server returns inline results (200, not 202). Don't chase this as a bug — it's the expected pattern for Foundry's MCP client.

### 2026-03-09 — Custom SF profiles without fieldPermissions hide all non-system fields
**Mistake:** The `Standard User - No Delete` profile only had `objectPermissions` (CRUD) but no `fieldPermissions`. The demo user could only see ~25 system fields on Contact (Id, Name, CreatedDate, etc.) — standard fields like AccountId, Email, Phone, Title were hidden. This broke all cross-object queries (Contact→Account lookups, subqueries).
**Root cause:** Salesforce custom profiles default all field-level security (FLS) to hidden. Only system fields are visible without explicit `fieldPermissions`. The profile granted object-level read/create/edit but never granted field-level access.
**Fix:** Created `MCP_Standard_Fields` permission set with FLS for key fields across Account, Contact, Opportunity, Case, Lead. Moved `userPermissions` (ApiEnabled, Lightning, etc.) from profile to permission set. Profile now only controls object-level CRUD (Account delete denied).
**Rule:** Use permission sets for FLS, not profiles. Profiles should only define object-level CRUD restrictions (the "no delete" use case). Permission sets are reusable, additive, and follow SF best practices. When creating custom profiles, always pair them with a permission set granting FLS on standard fields.

### 2026-03-09 — MCP server instructions are NOT injected into Foundry agent system prompt
**Mistake:** Spent multiple iterations adding detailed tool usage rules (availableFields, mode selection, error recovery) to the FastMCP `instructions` parameter. The agent ignored them — it still called `describe_object` after errors, used `mode="full"` for reads, etc.
**Root cause:** The FastMCP `instructions` field is part of the MCP server metadata, but Azure AI Foundry does NOT inject MCP server instructions into the agent's system prompt. The agent only sees: (1) its own `instructions` from `PromptAgentDefinition`, and (2) tool schemas from MCP `tools/list`. The detailed MCP server instructions were invisible to the model.
**Fix:** Moved critical rules into the agent's system prompt in `hooks/postprovision.py`. Key rules: workflow steps, mode selection (slim for reads, full for writes), availableFields usage (don't call describe after errors), common fields that don't need describe.
**Rule:** Put behavioral rules in the agent's `instructions` (postprovision.py), not in MCP server `instructions`. Tool parameter descriptions and return format docs go in tool docstrings (visible via `tools/list`). MCP server instructions are essentially documentation that no one reads.

### 2026-03-09 — Slim describe should include referenceTo and childRelationships
**Finding:** The original slim describe only returned `{name, type, required}` per field. The agent couldn't determine which fields are lookups to which objects, making cross-object queries (joins, subqueries) impossible without calling full describe.
**Fix:** Enhanced `_slim_describe` to include `referenceTo` for reference fields and compact `childRelationships` `[{name, object}]`. Also enriched `availableFields` in SOQL error responses with `{name, type, referenceTo}`. Token cost increase is minimal (most fields have null referenceTo).
**Rule:** Slim describe should always include relationship info — it's the minimum context needed for the agent to build cross-object SOQL queries. Full describe is only needed for picklist values and externalId flags (write operations).

### 2026-03-09 — Describe cache is per-object, not per-user (OBO cache poisoning risk)
**Finding:** `SalesforceClient._describe_cache` is keyed by `object_name` only. In OBO mode, different users have different FLS — user A's restricted describe result gets cached and served to user B (who may have full access). TTL is 15 minutes.
**Impact:** Low for single-user testing, but a real bug for multi-user production. A restricted user's cached describe would hide fields from admin users until cache expires.
**Rule:** For multi-user OBO deployments, the describe cache needs per-user keying (e.g., `(object_name, user_id)`) or must be disabled. Current single-user testing is unaffected.

### 2026-03-12 — MCPToolRequireApproval breaks in Teams (Bot Framework can't surface approval UI)
**Mistake:** Added `MCPToolRequireApproval1` with `always` for write tools. Worked fine in web chat but broke in Teams — all write operations failed with "MCP approval requests do not have an approval."
**Root cause:** The approval flow depends on the chat app's `/api/chat/approve` endpoint and JavaScript UI to render approve/deny buttons. In Teams, the Bot Framework connects directly to Foundry's `activityprotocol` endpoint — the chat app frontend is never loaded. Foundry sends an `mcp_approval_request`, but Teams/Bot Framework has no mechanism to surface or respond to it. The write call never reaches the MCP server.
**Log evidence:** MCP server logs showed `soql_query` and `describe_object` calls (reads) succeeded, but zero `write_record` calls. Foundry `RequestResponse` logs showed the agent API calls but `responseLength: 0` — the approval gate blocked execution before the MCP tool call.
**Fix:** Set `require_approval: "never"` and added a system prompt guardrail: "ALWAYS confirm with the user before any create, update, upsert, or delete operation." This gives conversational approval that works in both web chat and Teams.
**Rule:** Don't use `MCPToolRequireApproval` (SDK-level approval) when the agent is accessible via Teams/Bot Framework — the approval UI only works in custom web chat apps. Use system prompt guardrails for write safety in multi-channel deployments. Graduated to `~/.claude/knowledge/foundry-sdk.md`.

### 2026-03-18 — APIM MCP type migration: `type` vs `apiType` and backend pattern
**Finding:** APIM's native MCP API type uses `type: 'mcp'` (NOT `apiType: 'mcp'`). Requires a separate backend resource with `backendId` set to the resource name (not full ARM ID). `serviceUrl` must be null (backend handles routing). No operations needed — MCP type handles GET/POST/DELETE natively.
**Migration steps:** (1) Add backend resource, (2) Change API to `type: 'mcp'` with `mcpProperties`, (3) Pre-delete orphaned wildcard operations via ARM REST before Bicep deploy, (4) Delete any manually created test APIs and backends.
**Rule:** When creating MCP APIs in Bicep, use `type` (not `apiType`), always create a backend resource, and never define operations. When migrating from HTTP to MCP, pre-delete orphaned operations to avoid ARM conflicts. Graduated to `~/.claude/knowledge/azure-apim.md`.

### 2026-03-18 — Agent Application/Deployment REST API is ARM control plane, not Foundry data plane
**Mistake:** Initially called `PUT {projectEndpoint}/applications/...` (Foundry data-plane URL). `az rest` couldn't derive the auth resource, and with `--resource https://ai.azure.com` the data plane returned `UnsupportedApiVersion`.
**Root cause:** The `/applications/` and `/agentDeployments/` resources are registered ARM types under `Microsoft.CognitiveServices/accounts/projects/applications`, not Foundry data-plane endpoints. The data-plane URL routes to the agents backend which doesn't serve these resources.
**Fix:** Use `management.azure.com/subscriptions/.../providers/Microsoft.CognitiveServices/accounts/{account}/projects/{project}/applications/{app}?api-version=2026-01-15-preview`. No `--resource` needed (az rest auto-detects ARM).
**Rule:** Agent Application and Agent Deployment are ARM control-plane resources (supported api-versions: `2025-10-01-preview`, `2026-01-15-preview`). Always use `management.azure.com` paths, not Foundry data-plane URLs. Graduated to `~/.claude/knowledge/foundry-sdk.md`.

### 2026-03-18 — AppCatalog.ReadWrite.All role ID and managed tenant Teams restrictions
**Mistake:** Used wrong GUID for `AppCatalog.ReadWrite.All` application permission (`dc149144-f292-46f7-b616-e66f304c8cc9`). The correct ID is `dc149144-f292-421e-b185-5953f2e98d7f`.
**Root cause:** The IDs look similar (same prefix) but differ in the middle — likely a documentation/copypaste error.
**Additional finding:** Even with correct `AppCatalog.ReadWrite.All` token, managed MCAP tenants return 403 on `/appCatalogs/teamsApps` POST. This is a tenant-level Teams policy restriction, not a Graph API permission issue. Teams admin center policy must explicitly allow custom app uploads.
**Rule:** Always look up Graph app role IDs from the live service principal (`GET /servicePrincipals?$filter=appId eq '00000003-...'&$select=appRoles`), never from documentation. For managed tenants, Teams app catalog publish requires explicit Teams admin center policy change. Graduated to `~/.claude/knowledge/azure-identity.md`.

### 2026-03-18 — Bot Service msaAppId must be unique — adopt existing bots by msaAppId lookup
**Mistake:** Tried to create `agent-bot-sf-mcp-obo` while `agent-bot41626` (portal-created) already used the same msaAppId. ARM returned "MsaAppId is already in use".
**Root cause:** Bot Service enforces global uniqueness on msaAppId. Can't have two bot resources with the same msaAppId.
**Fix:** Bootstrap code lists all bots in the RG, finds the one with matching msaAppId, saves its name to `AGENT_BOT_NAME`. Bicep uses that name parameter to adopt the existing bot (idempotent PUT). No deletion needed.
**Rule:** Before creating a new Bot Service, always check if an existing bot already uses the target msaAppId. Adopt it by name rather than deleting and recreating. Graduated to `~/.claude/knowledge/foundry-sdk.md`.

### 2026-03-19 — Foundry memory_search_call data lives in Pydantic model_extra
**Finding:** The `memory_search_call` output item from Foundry's Responses API stores memory results in `item.model_extra['memories']`, not in standard attributes like `query`, `results`, or `output`. The item is typed as `ResponseOutputMessage` with `content=None`, `role=None`. All memory-specific data is in the extra fields dict.
**Discovery method:** Logged all attributes via `dir(item)` and found `model_extra` containing `{'memories': [...], 'agent_reference': {...}, 'response_id': '...', 'error': None}`.
**Rule:** For Foundry SDK items with `type='memory_search_call'`, always access `item.model_extra['memories']` for results. Each memory is a dict with text summaries of past conversations. Standard attributes (`content`, `role`) are None. Graduated to `~/.claude/knowledge/foundry-sdk.md`.

### 2026-03-19 — KQL: order by must use original column names, not project aliases
**Mistake:** Used `order by timestamp asc` after `project timestamp=TimeGenerated`. KQL returned `SemanticError: Failed to resolve column 'timestamp'`.
**Root cause:** KQL doesn't allow ordering by projected aliases in union queries.
**Fix:** Move `order by TimeGenerated asc` before the `project` statement.
**Rule:** In KQL, always `order by` using original column names, then `project` with aliases after.

### 2026-03-19 — KQL: session_id is in Message text, not Properties
**Mistake:** KQL queried `Properties.session_id` but the Python logger writes `session_id=xxx` as part of the log message string, not as a structured property.
**Root cause:** Python's `logger.info("chat_request session_id=%s", session_id)` produces a formatted string in `Message`, not structured key-value pairs in `Properties`. OTel only adds code location to Properties.
**Fix:** Changed KQL to `Message contains sid` and correlate via `OperationId` to find related traces across services.
**Rule:** When using Python stdlib logging with Azure Monitor OpenTelemetry, log arguments become part of the Message string. Use `Message contains` in KQL, not `Properties.key`. To get structured properties, use OTel's `logging.getLogger().info("msg", extra={"key": "value"})` pattern.

### 2026-03-19 — azd deploy overwrites env vars set by postprovision
**Mistake:** `azd deploy` creates a new Container App revision using the Bicep-defined env vars. Env vars added by postprovision via `az containerapp update` (like `CHAT_APP_ENTRA_CLIENT_ID`, `TENANT_ID`) are lost on every `azd deploy`.
**Root cause:** `azd deploy` regenerates the container spec from Bicep, which only had 4 env vars. The postprovision-added vars existed on the old revision but not in the Bicep template.
**Fix:** Added all postprovision-set env vars as conditional params in `chat-app.bicep` (using spread operator `...(!empty(param) ? [...] : [])`). Sourced from `main.bicepparam` via `readEnvironmentVariable()`.
**Rule:** Every env var that a Container App needs must be defined in Bicep, not just set via `az containerapp update`. Use conditional spread to avoid setting empty values on first deploy. The postprovision hook sets the azd env var; Bicep reads it on subsequent deploys.

### 2026-03-18 — CDN scripts blocked by browser Tracking Prevention
**Mistake:** Added `marked.js` via jsDelivr CDN (`cdn.jsdelivr.net`). Edge/Chrome Tracking Prevention blocked storage access for the CDN script, causing repeated "Tracking Prevention blocked access to storage" warnings.
**Root cause:** Browser privacy features block third-party CDN scripts from accessing storage (cookies, localStorage). jsDelivr is flagged by Tracking Prevention.
**Fix:** Downloaded `marked.min.js` locally and served from `/marked.min.js` via FastAPI static files.
**Rule:** For chat apps, bundle third-party JS libraries locally instead of using CDNs. CDN scripts are frequently blocked by browser privacy features (Tracking Prevention, Content Blockers). Self-hosting avoids this entirely.

### 2026-03-18 — MSAL popup blocked by Cross-Origin-Opener-Policy (COOP)
**Mistake:** Used `acquireTokenPopup()` as fallback when `acquireTokenSilent()` failed. The popup opened but MSAL couldn't detect when it closed — `Cross-Origin-Opener-Policy policy would block the window.closed call` repeated infinitely.
**Root cause:** Azure Container Apps (or the browser) adds COOP headers that prevent cross-origin popups from communicating back to the opener. MSAL's popup flow relies on `window.closed` to detect completion, which COOP blocks.
**Fix:** Switched fallback from `acquireTokenPopup` to `acquireTokenRedirect`. Added `handleRedirectPromise()` in `initialize()` to handle the redirect response on page reload.
**Rule:** For Container App-hosted SPAs, use `acquireTokenRedirect` (not `acquireTokenPopup`) as the MSAL fallback. Always call `handleRedirectPromise()` during initialization. Popup-based flows are unreliable with COOP headers. Graduated to `~/.claude/knowledge/azure-identity.md`.

### 2026-03-18 — Undefined function crashes sendMessage silently
**Mistake:** `addDebugLogEntry()` called `applyDebugFilters(entry)` which was never defined. This threw `ReferenceError` inside `debugLog()`, which was called at the start of `sendMessage()`. The entire `sendMessage()` function crashed before the `fetch` call — zero POST requests reached the server.
**Root cause:** The function was referenced during development but the implementation was inlined differently (as `filterDebugLogs()` for bulk filtering). The single-entry version was never created.
**Fix:** Inlined the filter logic directly in `addDebugLogEntry()`.
**Rule:** When adding debug/logging instrumentation to critical paths (like `sendMessage`), wrap in try-catch so logging failures don't break core functionality. Or: always search for undefined function references before deploying (`grep -n 'functionName' | grep -v 'function functionName'`).

### 2026-03-18 — Chat app markdown rendering requires sanitized HTML output
**Finding:** The Foundry agent returns markdown (tables, bold, lists, code blocks) in responses. The original `escapeHtml()` rendered these as plain text — tables were unreadable.
**Fix:** Added `marked.js` (bundled locally) for assistant message rendering. Added CSS for tables (alternating rows, sticky headers, hover), code blocks, lists, headings. User messages still use `escapeHtml()` (no markdown processing).
**Rule:** Always render assistant messages with a markdown parser. User messages should remain plain text (escaped). Pin/bundle the markdown library locally (see CDN lesson above).

### 2026-03-18 — Log Analytics Reader RBAC needed for debug log tail
**Mistake:** Added `GET /api/debug/logs` SSE endpoint that queries Log Analytics. The chat-app managed identity had Cognitive Services roles but no Log Analytics access. Every 4-second poll spammed `InsufficientAccessError`.
**Fix:** Assigned `Log Analytics Reader` role to the chat-app managed identity on the workspace. Changed SSE to on-demand (Fetch button) instead of auto-connect to avoid error spam.
**Rule:** When adding Log Analytics query features to an app, assign `Log Analytics Reader` to the app's managed identity on the workspace. Don't auto-connect SSE streams to authenticated endpoints — use on-demand fetch to avoid error spam when RBAC isn't configured yet.

### 2026-03-21 — Foundry-managed identity has no client secret — custom bot endpoint impossible

**Mistake:** Built a custom `/api/messages` Bot Framework endpoint assuming we could authenticate outbound replies. The adapter used `app_password=""` which caused `AADSTS7000216: 'client_secret' is required for the 'client_credentials' grant type` on every reply.
**Root cause:** The `AGENT_BOT_MSA_APP_ID` (`f94da8d7`) is a Foundry-managed identity — we cannot add a client secret to it. Bot Framework requires `client_credentials` to authenticate outbound replies to channels (Teams, DirectLine). Without a secret, the adapter can authenticate inbound JWTs but cannot send replies.
**Fix:** Two options: (1) Use Foundry's activity protocol (Foundry handles replies internally — no client_credentials needed), or (2) Create a separate confidential Entra app with a client secret for Bot Framework auth. Option 1 was chosen for stability; option 2 was implemented and rolled back.
**Rule:** Foundry-managed identities cannot be used as Bot Framework `app_password`. If you need a custom bot endpoint (`/api/messages`), you must create a dedicated confidential Entra app registration. The Foundry activity protocol is the path of least resistance for Teams integration.

### 2026-03-21 — Bot Framework InvokeResponse must go through send_activity, not callback return

**Mistake:** Changed `_handle_invoke()` to return `InvokeResponse(status=200)` from the callback. The adapter returned 501 NOT_IMPLEMENTED to Teams — SSO flow never completed.
**Root cause:** `BotFrameworkAdapter.process_activity` does NOT use the callback's return value. It reads invoke responses from `context.turn_state`, which is populated when you call `turn_context.send_activity(Activity(type="invokeResponse", value=InvokeResponse(...)))`. The adapter intercepts this activity type in `send_activities` and stores it in `turn_state`.
**Fix:** Use `await turn_context.send_activity(Activity(type="invokeResponse", value=InvokeResponse(status=200, body={})))` — never return from the callback.
**Rule:** In botbuilder-python, invoke responses MUST be sent via `send_activity` with `type="invokeResponse"`. The adapter's `send_activities` method intercepts these and stores them in `turn_state` for `process_activity` to return. Returning from the bot callback is ignored.

### 2026-03-21 — Bot Service msaAppId cannot be changed via PATCH

**Mistake:** Tried `PATCH` on Bot Service to change `msaAppId` from the Foundry-managed identity to the new confidential app. The PATCH returned 200 but the msaAppId was unchanged.
**Root cause:** Bot Service `msaAppId` is immutable after creation. PATCH silently ignores changes to this field.
**Rule:** To change a Bot Service's `msaAppId`, you must delete and recreate the bot. Or create a new bot with the desired msaAppId and update the Teams app manifest to point to it.

### 2026-03-21 — Deleting agent from Foundry UI corrupts application identity provisioning

**Mistake:** User deleted the `salesforce-assistant` agent from Foundry UI and republished. After this, the Agent Application's `defaultInstanceIdentity.provisioningState` was stuck at `"Creating"` indefinitely. Teams Copilot stopped working — agent responded without using MCP tools.
**Root cause:** Foundry platform issue. Deleting and republishing an agent from the UI puts the application identity in a corrupted state. API DELETE on the application returns `SystemError`. Creating new agents/applications in the same project also get stuck identity.
**Impact:** Teams Copilot broken (activity protocol path). Web chat and M365 Copilot web unaffected (they use Responses API directly, not activity protocol).
**Rule:** NEVER delete a published agent from the Foundry UI if Teams integration is working. To update the agent, use `create_version()` via SDK and update the deployment — don't delete/republish. If identity gets corrupted, the only known fix is a support ticket or clean deploy to a new resource group.

### 2026-03-21 — Teams Copilot vs Web Chat use completely different API paths

**Finding:** Web chat and M365 Copilot web use `openai_client.responses.create()` with `agent_reference` (Responses API). Teams Copilot goes through Bot Service → Foundry activity protocol. These are entirely separate code paths with different auth and identity requirements.
**Impact:** A broken Agent Application (stuck identity) blocks Teams Copilot but web chat continues working. Conversely, a broken bot endpoint blocks Teams but not web chat.
**Rule:** Always test both paths independently. Web chat working does NOT mean Teams works. The activity protocol depends on Agent Application identity provisioning; the Responses API does not.

### 2026-03-21 — Foundry UI publish changes traffic routing to new deployment

**Finding:** Publishing from Foundry UI creates a new deployment (e.g., `salesforce-assistant-17`) and updates the application's `trafficRoutingPolicy` to route 100% to the new deployment. Our deployment (`salesforce-assistant` pointing to v19) gets 0% traffic.
**Fix:** Can update traffic routing via ARM PUT on the application with `trafficRoutingPolicy.rules[].deploymentId` set to the desired deployment.
**Rule:** After Foundry UI publish, check `trafficRoutingPolicy` on the application. The UI publish overrides any existing routing. Use ARM REST to restore routing to the correct deployment if needed.

### 2026-03-21 — Container App secrets must use secretRef for sensitive values

**Finding:** Bicep Container App env vars with `value: botAppSecret` store the secret in plaintext (visible in Azure Portal and ARM API responses). The `@secure()` decorator on the Bicep param only prevents it from appearing in deployment logs.
**Fix:** Add the secret to the Container App `secrets[]` array, then reference it via `secretRef` in the env var instead of `value`.
**Rule:** For client secrets and other sensitive values in Container Apps, always use: (1) `secrets: [{ name: 'my-secret', value: param }]` in configuration, (2) `env: [{ name: 'MY_SECRET', secretRef: 'my-secret' }]` in template. Never use `value` directly for secrets.

### 2026-03-21 — az CLI CAE token requires account clear + re-login

**Mistake:** `az login` after CAE challenge (`TokenCreatedWithOutdatedPolicies`) didn't fix `az ad app list`. Token cache still held the stale token.
**Fix:** `az account clear && az login` — clears the token cache before re-authenticating.
**Rule:** For CAE challenges, always `az account clear` before `az login`. A simple `az login` may reuse cached tokens. Also verify subscription with `az account show` after re-login — managed tenants may default to the wrong subscription.

### 2026-03-26 — Multi-agent headless chat app + dynamic discovery

**Change:** Refactored the chat app from single-agent (hardcoded `salesforce-assistant`) to multi-agent headless architecture supporting any AI Foundry agent across any project.

**Key decisions:**
- `AIProjectClient.agents.list()` discovers agents dynamically — no manual config needed
- Azure Resource Graph discovers Foundry projects across the subscription
- Managed identity (not user token) for discovery — simpler, avoids ARM token scope issues
- `DefaultAzureCredential` for Resource Graph + agent listing; user token only for chat
- 5-min cache on discovery results to avoid API overhead
- `AGENTS_CONFIG` kept as static fallback for environments without managed identity access
- Frontend `AGENT_METADATA` mapping enriches dynamic agents with icons/prompts

**RBAC gotcha:** Container App managed identity needs:
- `Reader` at subscription level — for Resource Graph query
- `Cognitive Services User` on each AI Services account — for `agents.list()`
- The subscription Reader is in Bicep (`subscription-role-assignment.bicep`); cross-RG Cognitive Services User must be assigned manually per new account.

**`azd deploy` vs env vars:** `azd deploy` only pushes code, NOT env vars. Env vars set via `azd env set` only take effect after `azd provision` (Bicep) or manual `az containerapp update --set-env-vars`.

**Rule:** When adding new env vars to Bicep, always wire the full chain: `main.bicepparam` → `main.bicep` param → module param → container env. Missing any link means the var won't reach the container.

**Graduated:** Agent discovery/listing + RBAC rules → `~/.claude/knowledge/foundry-sdk.md` (Agent Discovery & Listing section).

### 2026-03-26 — httpx for Resource Graph REST API

**Decision:** Used `httpx` (async HTTP client) instead of `azure-mgmt-resourcegraph` SDK for Resource Graph queries.
**Why:** Avoids adding a heavy management SDK dependency. Resource Graph REST API is simple (single POST) and `httpx` is lightweight.
**Rule:** Prefer REST API + `httpx` over Azure management SDKs when the API surface is small (1-2 calls). Saves dependency weight and avoids SDK version conflicts.

### 2026-03-26 — Teams bot trafficRoutingPolicy stale after Agent Deployment update

**Problem:** When postprovision recreates an Agent Deployment (new agent version), the deployment gets a new `deploymentId`. But the Agent Application's `trafficRoutingPolicy` still points to the old `deploymentId`. The Responses API (web chat) works because it resolves agents by name, but the Activity Protocol (Teams/Copilot) routes via the Application's traffic policy and fails with "Sorry, I wasn't able to respond."

**Root cause:** `create_agent_deployment()` didn't update the Application's routing after deployment. The SF bot worked by coincidence (deployment ID hadn't changed recently).

**Fix:** Added `_update_traffic_routing()` function that PUTs the Application with the new `deploymentId` in `trafficRoutingPolicy` after every deployment creation.

**Rule:** After creating/updating an Agent Deployment, ALWAYS update the Agent Application's `trafficRoutingPolicy.rules[0].deploymentId` to match the new deployment. Added as automatic step in `create_agent_deployment()`.

### 2026-03-26 — Foundry-generated Teams manifest has broken defaults

**Problem:** Publishing a Foundry agent to Teams via the AI Foundry portal generates a manifest with `validDomains: []` and `webApplicationInfo.resource: "api://example.com"`. This breaks Bot Framework SSO — Teams can't complete the token exchange, so the bot shows "Sorry, I wasn't able to respond" instead of the Foundry login card.

**Root cause:** Foundry's manifest generator uses placeholder values instead of computing the correct `api://botid-{msaAppId}` resource URI.

**Rule:** Never trust Foundry-generated Teams manifests. Always verify:
- `validDomains` includes `"token.botframework.com"`
- `webApplicationInfo.resource` is `"api://botid-{msaAppId}"` (not `"api://example.com"`)
- `webApplicationInfo.id` matches the bot's `msaAppId`
If wrong, regenerate the manifest and re-publish via Teams Admin Center.

### 2026-03-30 — Agent identity "Creating" state is cosmetic, not a failure indicator
**Mistake:** Diagnosed a Teams agent-oauth 500 error as caused by Agent Application identity
`provisioningState: "Creating"` in the ARM control plane. Recommended deleting/recreating
the application. The error resolved on its own without any changes, while the state remained
`"Creating"`.
**Root cause:** The `agentIdentityBlueprint` and `defaultInstanceIdentity` provisioningState
fields can be permanently stuck at `"Creating"` even when the identities are fully functional
in Entra ID. The 500 was a transient Foundry platform issue on the `agent-oauth` service.
**Rule:** Do NOT treat `provisioningState: "Creating"` on Agent Application identities as a
root cause. The identities work independently of this ARM metadata field. For `agent-oauth`
500s, first check if it's transient (retry after a few minutes) before investigating
infrastructure. Also: SAML SSO (Salesforce login federation) and agent-oauth (Foundry user
token acquisition for Teams) are completely independent flows — changes to one cannot break
the other.

### 2026-04-02 — ServiceNow company field is a reference, not a text field
**Mistake:** Seeded SN incidents with `company: "Acme Corp"` using `sysparm_input_display_value=true`, but the company field resolved to NULL because "Acme Corp" didn't exist in `core_company` table.
**Root cause:** SN `company` field on incident/problem/change_request is a reference to `core_company`. `sysparm_input_display_value=true` accepts display names but does NOT auto-create missing companies — it silently stores NULL.
**Rule:** Always pre-create companies in `core_company` table before seeding SN records that reference them. Use `sn_ensure_companies()` pattern.

### 2026-04-02 — Trimming agent instructions too aggressively causes memory over-reliance
**Mistake:** Removed Tool Routing, Cross-System Workflow, and verbose Memory sections from C360 instructions (44% reduction). Agent stopped calling MCP tools entirely and answered all queries from memory/prior conversations.
**Root cause:** Without explicit "always fetch fresh data" guidance, the agent used MemorySearchTool data from prior test runs instead of querying SF/SN tools.
**Rule:** When optimizing agent instructions, always keep an explicit guardrail: "NEVER answer data questions from memory alone — always call tools for fresh data." Memory is for user preferences and metadata, not record-level data.

### 2026-04-02 — Foundry Responses API 429 rate limits are TPM-based
**Mistake:** E2E test with 6 heavy cross-system turns hit 429 at turn 4 despite 15s inter-turn delays.
**Root cause:** Rate limits are tokens-per-minute (TPM), not requests-per-minute. Three turns with 8+ tool calls each exhausted the 250K TPM quota.
**Rule:** E2E tests with multi-tool agents need 429 retry with exponential backoff (30s base) and inter-turn pacing (15s minimum). For production, consider conversation summarization after 3-4 turns.

### 2026-04-03 — Foundry conversation starters require BOTH definition and metadata fields
**Mistake:** Set `definition.conversation_starters` via REST API but starters didn't appear in Foundry UI.
**Root cause:** Foundry UI reads starter prompts from `metadata.starterPrompts` (newline-separated string), not from `definition.conversation_starters`. The SDK `PromptAgentDefinition` doesn't expose either field. Must use data plane REST API directly.
**Rule:** To set conversation starters programmatically: (1) create agent via SDK, (2) GET the version via REST, (3) add `definition.conversation_starters` (array of `{"text":"..."}`) AND `metadata.starterPrompts` (newline-joined string), (4) POST new version. Graduated to `~/.ai/knowledge/foundry-sdk.md`.

### 2026-04-02 — Windows encoding breaks on Unicode in agent responses
**Mistake:** E2E test crashed with `'charmap' codec can't encode character '\u2194'` when agent used arrow character in response.
**Root cause:** Python stdout defaults to `cp1252` on Windows, which can't encode all Unicode characters from GPT-5.4 responses.
**Rule:** Force UTF-8 stdout at script start: `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")`

<!-- Example format:
### YYYY-MM-DD — Short title
**Mistake:** What went wrong
**Root cause:** Why it happened
**Rule:** The rule that prevents recurrence
-->
