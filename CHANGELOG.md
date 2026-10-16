# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **v3 — Foundry skills:** six versioned skills in `skills/<name>/SKILL.md`:
  `salesforce-crm`, `servicenow-itsm`, `customer360-correlation`, `revenue-at-risk`,
  `meeting-prep` and `change-risk`. The postprovision hook (`ensure_skills`) uploads
  each one as a Foundry skill version, and only when its content changed. Agents
  are pinned to those exact versions.
- **v3 — Customer 360 toolbox (opt-in):** with `C360_TOOLS_MODE=toolbox`,
  `customer360-assistant` reaches one version-pinned Foundry Toolbox. The toolbox
  holds the SF and SN MCP servers, each on its own OAuth2 OBO connection, plus all
  skills. The default stays `direct` (one MCP tool per system) until the two-user
  identity gate of the toolbox spike passes (see `.ai/project-reference.md`).
- **v3 — staged publish:** each agent gets an immutable `<agent>-v<N>` deployment.
  The hook smoke-tests that exact version before routing, routes 100% of the
  application's traffic to it and asserts the route. It records the previous route,
  and `python hooks/postprovision.py --rollback` restores it. Agent, skill,
  deployment and routing failures now fail the hook (the Teams catalog step stays
  non-fatal).
- **v3 — Teams for both agents:** `customer360-assistant` now gets its own Bot
  Service (`botServiceC360` in `infra/main.bicep`; `C360_BOT_MSA_APP_ID` /
  `C360_BOT_NAME`) and its own Teams app. The manifest version is patch-bumped
  from the latest catalog `appDefinition` instead of a hard-coded `1.0.0`. The
  manual smoke checklist is in `docs/teams-integration.md`.
- **OAuth consent UX:** the web chat shows a sign-in card for each
  `oauth_consent_request` and re-sends the message. The custom bot sends sign-in
  links. The E2E script handles sequential SF/SN consents.
- **Project page:** `site/` is a Customer 360 Assistant landing page on GitHub Pages
  (story video, plug-board diagram, identity journey, extension pattern). It is
  deployed by `.github/workflows/pages.yml` and reachable at
  `ozgurkarahan.com/customer-360/`.
- **Visuals:** Remotion stills (`video/src/visuals/`, `npm run visuals`) for the
  README hero, trust and extend diagrams, plus a 1200×630 social card.

### Changed

- **v3 — lean system prompts:** domain know-how moved out of the prompts into
  skills. Measured with the model's tokenizer, the base prompt is 85 tokens for SF
  (v2: 585) and 128 for C360 (v2: 621). Prompt agents cannot load skills at runtime
  on this platform yet. `PromptAgentDefinition.skills` needs the GitHub Copilot
  harness (forbidden on our subscription), and toolbox skills are MCP *resources*
  that the Responses MCP tool does not read. So the hook compiles the exact skill
  versions into the instructions (`SKILLS_MODE=inline`, the default): 897 tokens
  for SF and 3,403 for C360, as a static and cacheable prefix.
  `SKILLS_MODE=native` attaches `SkillReference`s plus the harness once it is
  enabled.
- **v3 — idempotent agents:** a new agent or toolbox version is created only when
  its definition fingerprint changes. Conversation starters go through the SDK
  (no raw REST).
- **Chat app:** every tool call carries an explicit `system`
  (`salesforce` / `servicenow` / `unknown`) and the bare tool name. Toolbox calls
  arrive as `<server_label>___<tool>`.
- **E2E (`scripts/test_e2e_customer360.py`):** it attributes calls to SF/SN through
  that map and runs one or more turns per skill, plus a write-guard turn (no write
  without the user's confirmation). It targets the latest version, an exact version
  (`--path version`, pre-route) or the routed application (`--path app`,
  post-route), and exits non-zero on failure.
- **Dependencies:** `azure-ai-projects==2.8.0` (pinned) and `openai`;
  `azure-ai-agents` was dropped (unused).
- **README:** rewritten business-first: build the engagement layer once, let one
  agent act across all your systems of record, as the signed-in user. The
  env-var, project-structure and troubleshooting tables moved to
  `docs/installation.md`. Per-tool token counts are replaced by one measured
  figure: ~1,650 tokens for the seven tool definitions (~2,150 with the server
  instructions), counted from the server's `tools/list` with the o200k_base
  tokenizer.
- **Docs:** `docs/teams-integration.md` now describes the automated Teams
  publishing path and the v2 token audience (`api://<MCP_OAUTH_CLIENT_ID>`).
- **Docs:** `docs/deepdive.md` and the architecture diagram
  (`docs/diagrams/azure-architecture.*`, `site/assets/architecture.webp`) now
  show that measured figure and the `gpt-5.4` model deployment (was "GPT-4o").

### Removed

- **v3 — Foundry agent memory:** the agents no longer get the memory search tool or
  the `project-memory` store. The chat app's memory parsing and MEM debug source are
  gone, and so is the `text-embedding-3-small` deployment in Bicep. Incremental
  deploys keep the live embedding deployment and the store. Both are **shared**, so
  they are kept on `sf-mcp-obo`: `servicenow-assistant` (snow-meta-tool) still uses
  the `project-memory` store, and the Foundry IQ `blob-kb` index/knowledge source still
  embeds with `text-embedding-3-small`. Delete them only after those workloads move off.

### Fixed

- **Infra:** `azd provision` no longer resets `ca-chat-app` / `ca-sf-mcp` to the
  helloworld placeholder image. The modules now take an `imageName` param fed from
  `SERVICE_CHAT_APP_IMAGE_NAME` / `SERVICE_SALESFORCE_MCP_IMAGE_NAME` (written by
  `azd deploy`), falling back to helloworld only on a fresh environment. Previously a
  provision-only run took the demo down with ingress timeouts (incident 2026-07-28).

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
