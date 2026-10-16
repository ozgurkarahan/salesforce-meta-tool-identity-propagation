"""v3 go/no-go spike: Foundry Toolbox (SF + SN MCP + skills) with per-user OBO identity.

Run from the repo root with the azd env loaded (AI_FOUNDRY_PROJECT_ENDPOINT,
APIM_GATEWAY_URL, AZURE_RESOURCE_GROUP, COGNITIVE_ACCOUNT_NAME,
AI_FOUNDRY_PROJECT_NAME). Requires: pip install azure-ai-projects==2.8.0 azure-identity openai

  # 1. upload skills, create toolbox + 3 spike agents (as the deployer)
  python scripts/spike_toolbox_identity.py setup --toolbox-auth none
  #    other auth variants for agent -> toolbox: pmi | agentic | static
  # 2. run probes as the user currently logged in with `az login`
  python scripts/spike_toolbox_identity.py chat --agent spike-c360-toolbox --probe identity
  #    second user: AZURE_CONFIG_DIR=~/.azure-user2 az login ; then same command with that env
  # 3. summarize evidence collected in .spike/
  python scripts/spike_toolbox_identity.py report
  # 4. remove spike agents/toolbox/connection (skills kept unless --delete-skills)
  python scripts/spike_toolbox_identity.py cleanup

Evidence never contains tokens: only token claims (oid/upn), item types, tool names,
truncated outputs and truncated consent links.
"""

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO / "skills"
OUT_DIR = REPO / ".spike"
MODEL = os.environ.get("AGENT_MODEL", "gpt-5.4")

TOOLBOX = "spike-c360-toolbox"
TOOLBOX_CONN = "spike-toolbox-conn"
AGENT_TOOLBOX = "spike-c360-toolbox"
AGENT_SF_SKILLS = "spike-sf-skills"
AGENT_SF_SKILLS_HARNESS = "spike-sf-skills-harness"
SPIKE_AGENTS = [AGENT_TOOLBOX, AGENT_SF_SKILLS, AGENT_SF_SKILLS_HARNESS]

SF_TOOLS = ["whoami", "list_objects", "describe_object", "soql_query",
            "search_records", "write_record", "process_approval"]
SN_TOOLS = ["discover", "query", "write"]

LEAN_C360 = (
    "You are a Customer 360 assistant over Salesforce (CRM) and ServiceNow (ITSM). "
    "Use your skills for how to query each system and how to correlate them. "
    "Always fetch fresh data with tools. Confirm with the user before any write. "
    "Summarize in plain language and lead with monetary amounts."
)
LEAN_SF = (
    "You are a Salesforce assistant. Use your skills for how to query and update "
    "Salesforce. Tell the user your plan before calling tools. Confirm with the user "
    "before any write. Summarize in plain language; never dump raw JSON."
)

PROBES = {
    "identity": (
        "Call Salesforce whoami and tell me my Salesforce username. Then query the "
        "ServiceNow sys_user table with query sys_id=javascript:gs.getUserID() and "
        "fields user_name,email and tell me my ServiceNow user name."
    ),
    "identity-sf": "Call whoami and tell me my Salesforce username and UserId.",
    "skills": "Which skills do you have available? List their names only.",
    "c360": "Which accounts have open P1 or P2 incidents? Show the revenue at risk.",
    "write-guard": "Change the Industry of Acme Corp to Retail.",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def env(name, default=""):
    value = os.environ.get(name, default)
    if not value:
        sys.exit(f"Missing env var {name} (run: azd env get-values > .env and export it)")
    return value


def az(cmd):
    r = subprocess.run(f"az {cmd}", shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"  az {cmd.split()[0]} failed: {r.stderr.strip()[:400]}")
        return None
    out = r.stdout.strip()
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return out


def project_client():
    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential
    cred = AzureCliCredential()
    return AIProjectClient(endpoint=env("AI_FOUNDRY_PROJECT_ENDPOINT"), credential=cred,
                           allow_preview=True), cred


def token_claims(cred):
    tok = cred.get_token("https://ai.azure.com/.default").token
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return {k: claims.get(k) for k in ("oid", "upn", "preferred_username", "tid")}


def connection_url(name):
    sub = az("account show --query id -o tsv")
    return (f"https://management.azure.com/subscriptions/{sub}"
            f"/resourceGroups/{env('AZURE_RESOURCE_GROUP')}"
            f"/providers/Microsoft.CognitiveServices/accounts/{env('COGNITIVE_ACCOUNT_NAME')}"
            f"/projects/{env('AI_FOUNDRY_PROJECT_NAME')}/connections/{name}"
            f"?api-version=2025-04-01-preview")


def sanitize(obj, limit=400):
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def upload_skills(pc):
    """Same upload path as azd (content-compared, LF-normalized, new version only on change)."""
    sys.path.insert(0, str(REPO / "hooks"))
    import postprovision
    return postprovision.ensure_skills(pc)


def ensure_toolbox_connection(auth, toolbox_url):
    """Connection used by the agent's MCPTool to reach the toolbox endpoint."""
    auth_type = {"pmi": "ProjectManagedIdentity", "agentic": "AgenticIdentityToken"}[auth]
    body = {"properties": {
        "authType": auth_type, "category": "RemoteTool", "group": "GenericProtocol",
        "target": toolbox_url, "audience": "https://ai.azure.com",
        "metadata": {"type": "custom_MCP"}, "isSharedToAll": False,
    }}
    path = OUT_DIR / "conn.json"
    path.write_text(json.dumps(body))
    res = az(f'rest --method PUT --url "{connection_url(TOOLBOX_CONN)}" '
             f'--headers "Content-Type=application/json" --body "@{path}"')
    path.unlink()
    if res is None:
        sys.exit(f"Could not create connection {TOOLBOX_CONN} with authType {auth_type} "
                 "(record this as a spike finding)")
    print(f"  connection {TOOLBOX_CONN} ({auth_type}) -> {toolbox_url}")


def point_endpoint(pc, agent_name, version):
    from azure.ai.projects.models import (
        AgentEndpointConfig, FixedRatioVersionSelectionRule, ProtocolConfiguration,
        ResponsesProtocolConfiguration, VersionSelector)
    pc.agents.update_details(agent_name=agent_name, agent_endpoint=AgentEndpointConfig(
        version_selector=VersionSelector(version_selection_rules=[
            FixedRatioVersionSelectionRule(agent_version=version, traffic_percentage=100)]),
        protocol_configuration=ProtocolConfiguration(responses=ResponsesProtocolConfiguration()),
    ))


def cmd_setup(args):
    from azure.ai.projects.models import (
        GitHubCopilotHarness, MCPTool, MCPToolboxTool, PromptAgentDefinition,
        SkillReference, ToolboxSkillReference)
    OUT_DIR.mkdir(exist_ok=True)
    pc, cred = project_client()
    endpoint = env("AI_FOUNDRY_PROJECT_ENDPOINT")
    gw = env("APIM_GATEWAY_URL")
    sf_url = os.environ.get("APIM_SF_MCP_OBO_ENDPOINT") or f"{gw}/salesforce-mcp-obo/mcp"
    sn_url = f"{gw}/servicenow-mcp-obo/mcp"
    sf_conn = os.environ.get("SF_OBO_CONNECTION_NAME", "salesforce-obo-oauth2")
    sn_conn = os.environ.get("SN_OBO_CONNECTION_NAME", "servicenow-obo-oauth2")
    state = {"created": dt.datetime.now(dt.timezone.utc).isoformat(),
             "toolbox_auth": args.toolbox_auth, "deployer": token_claims(cred)}

    print("Uploading skills...")
    skills = upload_skills(pc)
    state["skills"] = skills

    print("Creating toolbox...")
    tb = pc.toolboxes.create_version(
        name=TOOLBOX,
        description="v3 spike: Salesforce + ServiceNow MCP (OAuth2 OBO) + skills",
        tools=[
            MCPToolboxTool(server_label="salesforce_mcp", server_url=sf_url,
                           project_connection_id=sf_conn, require_approval="never",
                           allowed_tools=SF_TOOLS),
            MCPToolboxTool(server_label="servicenow_mcp", server_url=sn_url,
                           project_connection_id=sn_conn, require_approval="never",
                           allowed_tools=SN_TOOLS),
        ],
        skills=[ToolboxSkillReference(name=n, version=v) for n, v in skills.items()],
    )
    toolbox_url = f"{endpoint}/toolboxes/{TOOLBOX}/versions/{tb.version}/mcp?api-version=v1"
    state["toolbox"] = {"name": TOOLBOX, "version": tb.version, "url": toolbox_url}
    print(f"  toolbox {TOOLBOX} v{tb.version}")

    tb_tool = {"server_label": "c360_toolbox", "server_url": toolbox_url,
               "require_approval": "never"}
    if args.toolbox_auth in ("pmi", "agentic"):
        ensure_toolbox_connection(args.toolbox_auth, toolbox_url)
        tb_tool["project_connection_id"] = TOOLBOX_CONN
    elif args.toolbox_auth == "static":
        print("  WARNING: static token expires in ~1h; baseline only, not a pass")
        tb_tool["authorization"] = cred.get_token("https://ai.azure.com/.default").token

    sf_direct = MCPTool(server_label="salesforce_mcp", server_url=sf_url,
                        project_connection_id=sf_conn, require_approval="never",
                        allowed_tools=SF_TOOLS)
    sf_skill = [SkillReference(name="salesforce-crm", version=skills["salesforce-crm"])]
    definitions = {
        AGENT_TOOLBOX: PromptAgentDefinition(model=MODEL, instructions=LEAN_C360,
                                             tools=[MCPTool(**tb_tool)]),
        AGENT_SF_SKILLS: PromptAgentDefinition(model=MODEL, instructions=LEAN_SF,
                                               tools=[sf_direct], skills=sf_skill),
        AGENT_SF_SKILLS_HARNESS: PromptAgentDefinition(
            model=MODEL, instructions=LEAN_SF, tools=[sf_direct], skills=sf_skill,
            harness=GitHubCopilotHarness()),
    }
    state["agents"] = {}
    for name, definition in definitions.items():
        try:
            av = pc.agents.create_version(agent_name=name, definition=definition)
            point_endpoint(pc, name, av.version)
            stored = pc.agents.get(agent_name=name)
            state["agents"][name] = {"version": av.version, "ok": True,
                                     "stored_skills": sanitize(
                                         getattr(av.definition, "skills", None))}
            print(f"  agent {name} v{av.version} (endpoint -> v{av.version}; "
                  f"agent_endpoint set={stored.agent_endpoint is not None})")
        except Exception as e:  # record, keep going: each failure is a finding
            state["agents"][name] = {"ok": False, "error": sanitize(str(e), 800)}
            print(f"  agent {name} FAILED: {sanitize(str(e), 300)}")

    (OUT_DIR / "state.json").write_text(json.dumps(state, indent=2))
    print(f"\nState written to {OUT_DIR / 'state.json'}")


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

def summarize_items(items):
    rows = []
    for it in items:
        t = getattr(it, "type", "unknown")
        row = {"type": t}
        if t == "mcp_list_tools":
            row["server_label"] = getattr(it, "server_label", "")
            row["tools"] = [getattr(x, "name", "") for x in (getattr(it, "tools", None) or [])]
        elif t == "mcp_call":
            row.update(server_label=getattr(it, "server_label", ""),
                       name=getattr(it, "name", ""),
                       arguments=sanitize(getattr(it, "arguments", ""), 300),
                       error=sanitize(getattr(it, "error", None) or "", 300),
                       output=sanitize(getattr(it, "output", None) or "", 300))
        elif t == "oauth_consent_request":
            link = getattr(it, "consent_link", "") or ""
            row.update(server_label=getattr(it, "server_label", ""),
                       consent_link=link.split("?")[0] + "?...")
        elif t == "message":
            row["text"] = sanitize("".join(getattr(c, "text", "") for c in
                                           (getattr(it, "content", None) or [])), 600)
        else:
            dump = it.model_dump() if hasattr(it, "model_dump") else str(it)
            row["raw"] = sanitize(dump, 800)
        rows.append(row)
    return rows


def wait_for_consent(timeout=None):
    """ENTER on a TTY; otherwise poll for .spike/consent.ok (lets a background run continue)."""
    if timeout is None:
        timeout = int(os.environ.get("SPIKE_CONSENT_TIMEOUT", "1800"))
    if sys.stdin.isatty() and not os.environ.get("SPIKE_CONSENT_FILE"):
        input("Press ENTER after consenting...")
        return True
    flag = OUT_DIR / "consent.ok"
    print(f"Waiting for {flag} (create it after consenting)...", flush=True)
    deadline = dt.datetime.now() + dt.timedelta(seconds=timeout)
    while not flag.exists():
        if dt.datetime.now() > deadline:
            print("Timed out waiting for consent (evidence still written)")
            return False
        time.sleep(5)
    flag.unlink()
    return True


def cmd_chat(args):
    OUT_DIR.mkdir(exist_ok=True)
    pc, cred = project_client()
    who = token_claims(cred)
    message = args.message or PROBES[args.probe]
    print(f"User: {who.get('upn') or who.get('preferred_username')} (oid {who.get('oid')})")
    print(f"Agent: {args.agent}  path: {args.path}  probe: {args.probe}\n> {message}\n")

    if args.path == "endpoint":
        client = pc.get_openai_client(agent_name=args.agent)
        extra = {}
    else:
        client = pc.get_openai_client()
        extra = {"extra_body": {"agent_reference": {"name": args.agent,
                                                    "type": "agent_reference"}}}

    turns = []
    prev = None
    for attempt in range(4):  # first call + up to 3 sequential consents (SF, SN, retry)
        kwargs = {"input": message, **extra}
        if prev:
            kwargs["previous_response_id"] = prev
        started = dt.datetime.now(dt.timezone.utc)
        try:
            resp = client.responses.create(**kwargs)
        except Exception as e:
            turns.append({"attempt": attempt, "error": sanitize(str(e), 1000)})
            print(f"ERROR: {sanitize(str(e), 500)}")
            break
        items = summarize_items(getattr(resp, "output", []) or [])
        usage = getattr(resp, "usage", None)
        turns.append({"attempt": attempt, "response_id": resp.id,
                      "seconds": (dt.datetime.now(dt.timezone.utc) - started).total_seconds(),
                      "items": items,
                      "usage": usage.model_dump() if hasattr(usage, "model_dump") else None})
        for row in items:
            print("  " + json.dumps(row, ensure_ascii=False)[:300])
        consents = [r for r in items if r["type"] == "oauth_consent_request"]
        if not consents:
            break
        full = [getattr(i, "consent_link", "") for i in resp.output
                if getattr(i, "type", "") == "oauth_consent_request"]
        print("\nConsent required. Open this link in a browser signed in as the SAME user:")
        print(f"  {full[0]}", flush=True)
        if not wait_for_consent():
            break
        prev = resp.id

    record = {"time": dt.datetime.now(dt.timezone.utc).isoformat(), "user": who,
              "agent": args.agent, "path": args.path, "probe": args.probe,
              "message": message, "turns": turns}
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = OUT_DIR / f"chat-{args.agent}-{who.get('oid', 'unknown')[:8]}-{args.probe}-{stamp}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"\nEvidence: {out}")


# ---------------------------------------------------------------------------
# report / cleanup
# ---------------------------------------------------------------------------

def cmd_report(_args):
    state = json.loads((OUT_DIR / "state.json").read_text()) if (OUT_DIR / "state.json").exists() else {}
    print("== setup ==")
    print(json.dumps({k: state.get(k) for k in ("toolbox_auth", "toolbox", "skills", "agents")},
                     indent=2)[:3000])
    print("\n== chats ==")
    for f in sorted(OUT_DIR.glob("chat-*.json")):
        r = json.loads(f.read_text())
        user = r["user"].get("upn") or r["user"].get("preferred_username") or r["user"].get("oid")
        types, servers, errors, consents, text = [], set(), [], 0, ""
        for t in r["turns"]:
            if "error" in t:
                errors.append(t["error"][:120])
                continue
            for it in t["items"]:
                types.append(it["type"])
                if it["type"] == "mcp_call":
                    servers.add(f"{it.get('server_label')}:{it.get('name')}")
                    if it.get("error"):
                        errors.append(it["error"][:120])
                if it["type"] == "oauth_consent_request":
                    consents += 1
                if it["type"] == "message":
                    text = it.get("text", "")
        print(f"- {r['time']} {user} {r['agent']} [{r['path']}] probe={r['probe']}")
        print(f"    consents={consents} calls={sorted(servers)}")
        print(f"    item types={sorted(set(types))}")
        if errors:
            print(f"    errors={errors}")
        print(f"    answer={text[:250]!r}")


def cmd_cleanup(args):
    pc, _ = project_client()
    for name in SPIKE_AGENTS:
        try:
            pc.agents.delete(agent_name=name)
            print(f"  deleted agent {name}")
        except Exception as e:
            print(f"  agent {name}: {sanitize(str(e), 120)}")
    try:
        pc.toolboxes.delete(TOOLBOX)
        print(f"  deleted toolbox {TOOLBOX}")
    except Exception as e:
        print(f"  toolbox: {sanitize(str(e), 120)}")
    az(f'rest --method DELETE --url "{connection_url(TOOLBOX_CONN)}"')
    if args.delete_skills:
        for skill_dir in SKILLS_DIR.iterdir():
            try:
                pc.beta.skills.delete(skill_dir.name)
                print(f"  deleted skill {skill_dir.name}")
            except Exception as e:
                print(f"  skill {skill_dir.name}: {sanitize(str(e), 120)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--toolbox-auth", choices=["none", "pmi", "agentic", "static"], default="none")
    c = sub.add_parser("chat")
    c.add_argument("--agent", choices=SPIKE_AGENTS, required=True)
    c.add_argument("--probe", choices=sorted(PROBES), default="identity")
    c.add_argument("--message", help="override the probe text")
    c.add_argument("--path", choices=["endpoint", "reference"], default="endpoint",
                   help="endpoint = published agent endpoint; reference = agent_reference (v2 path)")
    sub.add_parser("report")
    d = sub.add_parser("cleanup")
    d.add_argument("--delete-skills", action="store_true")
    args = p.parse_args()
    {"setup": cmd_setup, "chat": cmd_chat, "report": cmd_report, "cleanup": cmd_cleanup}[args.cmd](args)


if __name__ == "__main__":
    main()
