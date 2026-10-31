# V3 Managed Harness staging

This additive staging path uses the confirmed Microsoft Copilot preview harness.
It does not convert `salesforce-assistant` or `customer360-assistant`, run the
root postprovision hook, provision infrastructure, or alter app/Teams routes.
Dynamic discovery may list the new agents; no production selector or route is changed.

## Frozen target and resources

Project: `aiproj-sf-mcp-obo` in subscription
`44026b8b-9f88-44d9-8f46-0898baa4bcd5`, resource group `rg-sf-mcp-obo`.
Endpoint:
`https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo`.
The existing `gpt-5.4-pro` model is reused, not created or changed.

| Resource | Name | Scope |
|---|---|---|
| Prompt agent | `salesforce-assistant-v3` | Salesforce only; `salesforce-crm@2` |
| Prompt agent | `customer360-assistant-v3` | Salesforce + ServiceNow; all six local skills at v2 |
| Toolbox | `salesforce-toolbox-v3` | `salesforce-obo-oauth2`, seven SF tools |
| Toolbox | `customer360-toolbox-v3` | Same SF tools + `servicenow-obo-oauth2`: `discover`, `query`, `write` |
| Toolbox connection | `salesforce-toolbox-v3-v1` | First-party Foundry toolbox version 1 only |
| Toolbox connection | `customer360-toolbox-v3-v1` | First-party Foundry toolbox version 1 only |

SF tools: `whoami`, `list_objects`, `describe_object`, `soql_query`,
`search_records`, `write_record`, `process_approval`.
Combined skill pins: `salesforce-crm`, `servicenow-itsm`,
`customer360-correlation`, `revenue-at-risk`, `meeting-prep`, `change-risk`.

Every MCP entry uses `require_approval: always`, including the outer toolbox
attachment. Writes still require the user's explicit confirmation of the exact
record, operation and values before requesting tool approval. Read approvals
must not become a blanket authorization for writes.

## Preflight

Run `python scripts\test_v3_staging.py` from the repository root. Root tooling
dependencies are in `requirements.txt`; do not install them unless missing.

Use only the already signed-in account. Never automatically run `az login`,
change accounts, modify consent, assign roles, or print credential-bearing
connection records. Snapshot existing agent versions/endpoints, OAuth2 connection
metadata and application traffic routes before deployment; compare them afterward.

Verify both named connections with `azd ai connection show` (without
`--show-credentials`): `RemoteTool`, `OAuth2`, and their exact APIM targets in
the staging manifest. Check the existing model is `Succeeded`.
Verify each pinned Foundry skill v2 exists and its downloaded package matches
`skills\<name>` after normalizing text line endings. If a package differs, stop
and review a new skill version rather than overwriting shared production pins.

## Scoped deployment (PowerShell)

Run from `staging\v3`, not the repository root. These commands use only
data-plane agent/toolbox resources in the existing project. Disable automatic
role assignment; missing permissions are a gate, not permission to change RBAC.

```powershell
$env:AZURE_DEV_USER_AGENT = 'microsoft_foundry_skill'
$env:AZD_AGENT_SKIP_ROLE_ASSIGNMENTS = 'true'
$env:AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS = 'true'
$project = 'https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo'
$subscription = '44026b8b-9f88-44d9-8f46-0898baa4bcd5'
azd env new v3-staging --subscription $subscription --no-prompt
azd env set FOUNDRY_PROJECT_ENDPOINT $project
azd env set AZURE_AI_PROJECT_ENDPOINT $project
azd env set AZURE_RESOURCE_GROUP rg-sf-mcp-obo
azd deploy salesforce-toolbox-v3 --no-prompt
azd deploy customer360-toolbox-v3 --no-prompt
```

On later runs, select the existing `v3-staging` environment instead of creating
another one. Check every command's exit status before proceeding. Do not run
`azd up`, `azd provision`, `azd deploy --all`, or `hooks\postprovision.py`.
Set `AZD_ALPHA_ENABLE_AGENTS_MANAGED_HARNESS=true` in the current process for
every targeted agent deployment/invocation. Keep it and the user-agent / role-skip
flags process-local; never persist them with `azd env set` or in the manifest.
The installed azd extension does not support deployment preview for prompt
agents or toolboxes: toolbox previews are skipped, and agent previews reject
anything except `kind: hosted`. Do not treat a skipped preview as validation,
convert the agent type, or retry preview. Use the offline contract checks,
review the manifest, then verify the exact published definitions after the
approved targeted deployment.

Read each exact version using `azd ai toolbox show <name> --version 1
--project-endpoint $project -o json`. Verify the tools, allowlists, approvals
and absence of toolbox skills. Obtain the returned **versioned** MCP endpoint.
The manifest pins version 1; if deployment returned another version, review it
and explicitly repin the agent and create a distinct version-bound connection.
Never repoint a connection used by another version.

Create the two new first-party toolbox connections, using each exact returned
versioned URL as `$sfEndpoint` / `$c360Endpoint`:

```powershell
azd ai connection create salesforce-toolbox-v3-v1 `
  --kind remote-tool --auth-type user-entra-token --audience https://ai.azure.com `
  --target $sfEndpoint --metadata type=toolbox_MCP `
  --metadata toolboxName=salesforce-toolbox-v3 --metadata toolboxVersion=1 `
  --project-endpoint $project
azd ai connection create customer360-toolbox-v3-v1 `
  --kind remote-tool --auth-type user-entra-token --audience https://ai.azure.com `
  --target $c360Endpoint --metadata type=toolbox_MCP `
  --metadata toolboxName=customer360-toolbox-v3 --metadata toolboxVersion=1 `
  --project-endpoint $project
azd deploy salesforce-assistant-v3 --no-prompt
azd deploy customer360-assistant-v3 --no-prompt
```

If a toolbox connection already exists, read it and reuse it only if target,
category, auth type, audience and metadata all match. Never use `--force`.
These UET connections target Foundry itself, not APIM. Backend authentication
still uses exactly `salesforce-obo-oauth2` / `servicenow-obo-oauth2`; never change
either or use `salesforce-obo` / `servicenow-obo`.

## Published definition and read-only runtime checks

Use `azd ai agent show <service> -o json` and read the exact version's definition.
Assert `kind=prompt`, `harness.type=microsoft_copilot_preview`,
`model=gpt-5.4-pro`, the versioned toolbox URL, its matching short connection
name, `require_approval=always`, the namespaced allowlist, and the exact
top-level skill name/version pairs. A successful deploy that loses any of these
fields is a failure.
Run `python scripts\test_v3_staging.py --live` from the repository root to assert
the published contracts, toolbox authentication and local/pinned skill equality.
These checks are read-only and do not invoke agents or start consent. Foundry
normalizes agent allowlists to `allowed_tools.tool_names` and may sort skill
references; validate their contents, not the original serialization order.

Invoke each staged agent with `azd ai agent invoke <service> --protocol responses
--new-conversation` and a read-only current-user prompt:

- Salesforce: call `salesforce_mcp___whoami` once.
- Customer 360: call that same SF tool and
  `servicenow_mcp___query(table="sys_user",
  query="sys_id=javascript:gs.getUserID()", fields="user_name,email", limit=1)`.

Inspect actual tool evidence, not just the assistant's identity claim.
The verification request approves only these read operations. If the runtime
requests tool approval, approve only the exact read name and arguments; never
approve `write_record`, `process_approval`, `write`, or an unrelated query.
Keep tokens, consent URLs, raw personal identities and backend records out of
committed evidence.

For SDK-based diagnostics, create and use the conversation through
`project_client.get_openai_client(agent_name=<staged-agent>)`, with the current
user credential and `Foundry-Features: MicrosoftCopilot=V1Preview`. The harness
requires `conversation=<id>`; a conversation created through the project-level
OpenAI client is not visible at the agent-scoped endpoint. Delete the verification
conversation through that same agent-scoped client afterward. Serialize unknown
preview response items without emitting SDK warnings or raw consent data.

If consent is required or the tool's identity does not match the current account,
report the system and gate. Do not launch consent or test a second user.
Run a separate write-guard turn asking to change a record without confirming:
there must be no write execution, and the agent must request confirmation.
No throwaway agents, applications, toolboxes, auth connections or infrastructure
are needed for these checks.

Compare the production snapshot after all checks. Current-user success is not
two-user isolation proof and does not authorize production cutover.

## Verified staging result (2026-10-31)

Both agents are enabled, with active version **1**. Both toolboxes published
version **1**, and their exact versioned endpoints match the two approved new
UET connections. Published models, Microsoft harness discriminators,
allowlists, approval policies, instructions and skill name/version pairs match
the manifest. Toolbox `skills` lists are empty; native skills are pinned only
on the agents.
`python scripts\test_v3_staging.py --live` passed **11/11** checks, covering the
offline manifest contract and the live agent/toolbox/auth/skill-package contracts.

The read-only current-user requests reached both staged agent endpoints, but
each returned `oauth_consent_request`: `salesforce_toolbox` for the SF agent,
`c360_toolbox` for Customer 360. **Backend identity verification remains gated
on consent.** No consent URL was opened, no second account was used, and no
backend read or write was approved or executed by the verification client.
The same current account/tenant was verified for azd and Azure CLI credentials.
All SDK verification conversations were deleted.

Sanitized consent metadata:

| Request server label | Bound toolbox connection | Configured backend OAuth2 connection(s) |
|---|---|---|
| `salesforce_toolbox` | `salesforce-toolbox-v3-v1` | `salesforce-obo-oauth2` |
| `c360_toolbox` | `customer360-toolbox-v3-v1` | `salesforce-obo-oauth2`, `servicenow-obo-oauth2` |

The completed consent request items do **not** expose scope or connection-name
fields; the bindings above come from the verified deployed definitions, not
inferred runtime identity. Both backend connection configurations have the
sanitized scope labels `offline_access` and `[application]/access_as_user`
(application-specific prefixes omitted). No consent URL, token or personal
identity is retained here.

The azd CLI attempt also created one Salesforce verification conversation before
returning the consent gate. A read-only agent-scoped inventory found it; its
stored user message matched this session's exact CLI verification prompt before
deletion. It was then deleted. **Both staged agents' current-user conversation
inventories are empty**, including CLI- and SDK-created verification state.

End-state hashes match the pre-deploy snapshot for all **11 existing agents**,
both backend OAuth2 connections and all **6 Agent Applications**; no Agent
Application was added. The existing `gpt-5.4-pro` deployment's ETag and capacity
are unchanged. Root app/server code, infrastructure, hooks, local skill sources
and routing configuration have no changes.

One toolbox-connection create initially failed in azd's credential discovery.
After checking the existing sign-in and successful token acquisition (without
printing tokens or starting login), the missing connection was created once,
without overwrite. No auth configuration or role assignment was changed.
