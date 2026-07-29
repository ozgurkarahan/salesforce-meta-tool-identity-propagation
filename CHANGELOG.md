# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-07-29

### Why this release exists

Microsoft changed the security posture of Foundry Agent Service for MCP tools.
Agent Service now **refuses to send Microsoft-audience Entra tokens to custom
MCP endpoints**. Our v1 identity propagation relied on exactly that: a
`UserEntraToken` connection that forwarded the user's `https://ai.azure.com`-audience
token to our APIM gateway. Since the platform change, every agent call wired to a
`UserEntraToken` connection fails before any APIM/backend traffic with:

> `Cannot pass Microsoft token to untrusted MCP endpoint`

This restriction is now officially documented in Microsoft Learn —
[Set up authentication for Model Context Protocol (MCP) tools → OAuth identity passthrough](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication#oauth-identity-passthrough):

> "When using managed OAuth with Microsoft Entra, Agent Service restricts tokens
> scoped to a known Microsoft audience from being sent to custom or third-party
> MCP servers. If you attempt this, Agent Service returns the error:
> `Cannot pass Microsoft token to untrusted MCP endpoint.`
> Your custom MCP server must be registered with an audience that you control
> rather than a known Microsoft audience. Don't design your MCP server to rely
> on passthrough of its authentication token to a downstream Microsoft service."

The trusted-endpoint list is Microsoft-managed with **no customer opt-in**.

Timeline of our findings:

| Date | Observation |
|------|-------------|
| 2026-03-01 | v1 (`UserEntraToken`, audience `https://ai.azure.com`) verified end-to-end; per-user identity confirmed in SF Login History |
| 2026-05-22 | First `Cannot pass Microsoft token to untrusted MCP endpoint` failures on agents wired to `UserEntraToken` connections |
| 2026-06-24 | Trust check transiently flapped off (~14:00-21:35 UTC) — UET worked again, then the block returned. A postprovision hook that recreated the connection as UET on every `azd up` silently reverted live fixes and took down every agent |
| 2026-07-27 | Clean-project re-test: fresh UET connection to our APIM endpoint fails in both URL-mode and connection-mode; Microsoft Learn now documents the restriction. Verdict: **UserEntraToken is permanently dead for custom MCP endpoints** |

v2 replaces the connection layer with **OAuth2 identity passthrough** against a
customer-owned Entra app registration — the pattern Microsoft Learn prescribes.
The three-phase APIM token exchange (Entra JWT → SF username → per-user SF
token) is unchanged; only how Foundry obtains the user's delegated token changed.

### Changed

- **Foundry connection**: `salesforce-obo` (`authType: UserEntraToken`) →
  `salesforce-obo-oauth2` (`authType: OAuth2`). Foundry now runs a standard
  OAuth2 authorization-code flow against our own Entra app
  (`MCP_OAUTH_CLIENT_ID`) with scopes `api://<appId>/access_as_user` +
  `offline_access`. The token APIM receives now carries audience
  `api://<MCP_OAUTH_CLIENT_ID>` instead of `https://ai.azure.com`.
- **Connection provisioning** moved from Bicep to the postprovision hook
  (`ensure_obo_connection()` in `hooks/postprovision.py`). The hook is
  **create-only**: it never deletes or overwrites an existing connection
  (guards against a repeat of the 2026-06-24 outage). Bicep cannot create this
  connection type because it needs a client secret plus per-connection redirect
  registration.
- **APIM `validate-jwt`** accepts the new audience `api://{{McpOauthClientId}}`
  (new Named Value, wired from `MCP_OAUTH_CLIENT_ID`); the legacy
  `https://ai.azure.com` audience is kept for backward compatibility only.
- **APIM error responses** now surface the real `validate-jwt` failure reason
  (`TokenExpired`, `TokenInvalidAudience`, `TokenNotPresent`, ...) as a JSON
  body plus `X-Auth-Error-Reason` header, instead of the generic
  `Invalid Azure AD token`. `TokenExpired` responses include re-consent
  guidance. Only reason/expiry are echoed — never audience/issuer details.
- **Customer 360 agent** pre-checks that the ServiceNow connection is OAuth2
  and skips creation with a clear warning otherwise
  (`SN_OBO_CONNECTION_NAME`, default `servicenow-obo-oauth2`).
- `gpt-5.4` model deployment capacity codified at 250 (was 120 in Bicep, 250
  live) so `azd up` no longer downgrades it.

### Added

- `MCP_OAUTH_CLIENT_ID` / `MCP_OAUTH_CLIENT_SECRET` environment variables —
  the Entra app registration backing the OAuth2 connections.
- `McpOauthClientId` APIM Named Value (inert `api://not-configured` audience
  until the env var is set).
- Automatic **redirect URI registration**: each ApiHub OAuth2 connection mints
  its own `redirectUrl`; the hook PATCHes it (plus the
  `consent.azure-apim.net` variant) onto the OAuth app's `web.redirectUris` —
  without this, user consent fails with `AADSTS50011`.
- This `CHANGELOG.md`.

### Removed

- `infra/modules/sf-obo-connection.bicep` — the Bicep-managed `UserEntraToken`
  connection. The `SF_OBO_CONNECTION_NAME` output is now pinned to
  `salesforce-obo-oauth2`.

### Migration from v1 (existing deployments)

1. Create (or reuse) an Entra app registration that exposes the scope
   `api://<appId>/access_as_user` and has a client secret:
   ```bash
   az ad app create --display-name mcp-oauth-passthrough
   # set identifierUris to api://<appId>, add the access_as_user scope,
   # then create a client secret
   ```
2. Configure the environment and redeploy:
   ```bash
   azd env set MCP_OAUTH_CLIENT_ID  "<appId>"
   azd env set MCP_OAUTH_CLIENT_SECRET "<secret>"
   azd up
   ```
   The hook creates `salesforce-obo-oauth2`, registers its redirect URI, and
   rewires the agents to it.
3. Verify: agent call → first call per user returns an `oauth_consent_request`
   link → complete the one-time consent → tool calls succeed; SF Login History
   shows the per-user entries.
4. Optional cleanup: delete the dead `UserEntraToken` connections
   (`salesforce-obo`, `servicenow-obo`) once no agent references them.

### Known issues (platform, not fixable in this repo)

- **ApiHub never uses the stored refresh token.** Despite `offline_access`
  being granted and a refresh URL being configured, the connection replays the
  stale access token after expiry (~1h): Entra logs `AADSTS90009` with
  `incomingTokenType=none`, APIM returns `401 TokenExpired`, and the user must
  start a new conversation and re-consent. Reproduced and forensically logged
  across Entra sign-in logs, APIM diagnostics, and Foundry conversation records
  (internal evidence retained offline — contains tenant identifiers);
  independent public report:
  [microsoft-foundry/new-foundry-portal#134](https://github.com/microsoft-foundry/new-foundry-portal/issues/134).
  The v2 APIM error surface exists precisely to make this failure mode
  self-explanatory to end users.
- The Foundry trust list for Microsoft-audience tokens is not customer-configurable;
  if Microsoft ever opens an opt-in, `UserEntraToken` (no per-user consent, no
  refresh bug) would again be the simpler design.

## [1.0.0] - 2026-03-01

Initial release: metadata-driven Salesforce MCP server (7 tools) with true
On-Behalf-Of identity propagation via a Foundry `UserEntraToken` connection and
a three-phase APIM token exchange (Entra JWT validation → SOQL username lookup
→ per-user JWT Bearer exchange). Verified end-to-end with per-user entries in
SF Login History. Superseded by 2.0.0 after the Foundry platform change above.
