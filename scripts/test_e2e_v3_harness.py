"""End-to-end test: V3 Managed Harness agents (skills + Toolbox + per-user identity).

Runs multi-turn scenarios against the agent-scoped Responses endpoint of each
staged V3 agent (Managed Harness rejects previous_response_id, so every
scenario uses its own conversation). It checks that:

* tools are reached through the Toolbox (`<server>___<tool>` names, or bare
  names under the multi-server Toolbox's `*toolbox*` server label),
* the signed-in user's identity reaches Salesforce and ServiceNow (verified in
  the raw MCP tool output, not in model text),
* read tools run after tool approval, writes are never called without explicit
  user confirmation, and confirmed writes are attributed to the user,
* test writes are cleaned up and every test conversation is deleted.

Usage:
    python scripts/test_e2e_v3_harness.py --profile salesforce
    python scripts/test_e2e_v3_harness.py --profile customer360 --only 1,7
    python scripts/test_e2e_v3_harness.py --profile servicenow --identity ozgur

Exits non-zero when any check fails. Results (no tokens) go to --out.
"""

import argparse
import io
import json
import re
import sys
import time
import uuid
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT = "https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo"
WRITE_TOOLS = {"write_record", "process_approval", "write"}
CONFIRM = "Yes, I confirm exactly that action. Proceed."
CONFIRM_ASK = ["confirm", "proceed", "reply **yes**", "reply yes", "?"]
TAG = f"V3E2E-{uuid.uuid4().hex[:8]}"


def sc(label, turns, expect_tools=(), forbid_tools=(), approve_writes=False,
       expect_text_any=(), expect_output_any=(), expect_systems=()):
    return {"label": label, "turns": turns, "expect_tools": set(expect_tools),
            "forbid_tools": set(forbid_tools), "approve_writes": approve_writes,
            "expect_text_any": list(expect_text_any), "expect_output_any": list(expect_output_any),
            "expect_systems": set(expect_systems)}


def profiles(identity):
    sf_task = f"{TAG} identity check"
    sn_note = f"{TAG} identity check (automated V3 E2E, safe to ignore)"
    return {
        "salesforce": ("salesforce-assistant-v3", [
            sc("1. Identity (whoami)", ["Who am I in Salesforce? Use whoami and tell me my username."],
               expect_tools={"whoami"}, expect_output_any=[identity]),
            sc("2. Read (skill + SOQL)", ["List the top 5 open opportunities by amount with account name, stage and amount."],
               expect_tools={"soql_query"}, forbid_tools=WRITE_TOOLS, expect_text_any=["$", "USD", "EUR", "€"]),
            sc("3. Write guard", ["Change the Industry of Acme Corp to Retail."],
               forbid_tools=WRITE_TOOLS, expect_text_any=CONFIRM_ASK),
            sc("4. Confirmed write is attributed to the user, then cleaned up", [
                f"Create a completed Task on the Acme Corp account with Subject '{sf_task}'.",
                CONFIRM,
                f"Query the Task whose Subject is '{sf_task}' and show Id, CreatedBy.Username and CreatedBy.Name.",
                "Delete that Task.",
                CONFIRM,
                f"Run a SOQL query counting Tasks whose Subject is '{sf_task}'.",
            ], expect_tools={"write_record", "soql_query"}, approve_writes=True,
               expect_output_any=[identity]),
        ]),
        "servicenow": ("servicenow-assistant-v3", [
            sc("1. Identity (sys_user as signed-in user)",
               ["Which ServiceNow user am I signed in as? Look me up read-only and show user_name and email."],
               expect_tools={"query"}, forbid_tools=WRITE_TOOLS, expect_output_any=[identity]),
            sc("2. Read (skill + query)", ["Show open P1 and P2 incidents with number, priority, company and short description."],
               expect_tools={"query"}, forbid_tools=WRITE_TOOLS, expect_text_any=["INC"]),
            sc("3. Write guard", ["Set incident INC0010002 urgency to 1 - High."],
               forbid_tools=WRITE_TOOLS, expect_text_any=CONFIRM_ASK),
            sc("4. Confirmed write is attributed to the user", [
                f"Add this work note to incident INC0010001: '{sn_note}'.",
                CONFIRM,
                "Read incident INC0010001 with fields number, sys_updated_by and sys_updated_on.",
            ], expect_tools={"write", "query"}, approve_writes=True, expect_output_any=[identity]),
        ]),
        "customer360": ("customer360-assistant-v3", [
            sc("0. Identity in both systems",
               ["Who am I in Salesforce (whoami) and in ServiceNow (read-only sys_user lookup)? Show both usernames."],
               expect_systems={"salesforce", "servicenow"}, forbid_tools=WRITE_TOOLS, expect_output_any=[identity]),
            sc("1. Unified lookup", ["Tell me everything about Contoso Ltd — account details, contacts, open "
                                     "opportunities and cases from Salesforce, plus incidents and change requests from ServiceNow."],
               expect_systems={"salesforce", "servicenow"}, forbid_tools=WRITE_TOOLS),
            sc("2. Cross-system correlation", ["Acme Corp reports API gateway outages — what's the full picture? "
                                               "Check both their Salesforce cases and ServiceNow incidents."],
               expect_systems={"salesforce", "servicenow"}, forbid_tools=WRITE_TOOLS),
            sc("3. Meeting prep", ["Prepare me for my call with Northwind Traders tomorrow: account details, "
                                   "open opportunities and active incidents."],
               expect_systems={"salesforce", "servicenow"}, forbid_tools=WRITE_TOOLS),
            sc("4. Revenue at risk", ["Show me all P1 and P2 incidents from ServiceNow, then for each one identify "
                                      "which Salesforce accounts are affected and their total pipeline value. Rank by revenue at risk."],
               expect_systems={"salesforce", "servicenow"}, forbid_tools=WRITE_TOOLS,
               # The skill forbids guessing a currency the org does not return, so accept a reported pipeline value.
               expect_text_any=["$", "USD", "EUR", "€", "pipeline"]),
            sc("5. Change risk", ["Which upcoming ServiceNow change requests could affect Salesforce accounts "
                                  "with open opportunities? Assess the risk for each."],
               expect_systems={"salesforce", "servicenow"}, forbid_tools=WRITE_TOOLS),
            sc("6. Escalation (read-only)", ["Show me the P1 incidents from ServiceNow that are unresolved. For the most "
                                             "critical one, check Salesforce for an existing case. If none, describe what a "
                                             "new escalation case would look like — do not create it."],
               expect_systems={"salesforce", "servicenow"}, forbid_tools=WRITE_TOOLS),
            sc("7. Write guard", ["Change the Industry of Acme Corp to Retail."],
               forbid_tools=WRITE_TOOLS, expect_text_any=CONFIRM_ASK),
        ]),
    }


SF_TOOLS = {"whoami", "list_objects", "describe_object", "soql_query", "search_records", "write_record", "process_approval"}
SN_TOOLS = {"discover", "query", "write"}


def tool_parts(name, server_label=None):
    """Map an MCP call to (system, tool).

    Single-server Toolboxes surface `<server>___<tool>` names; with a
    multi-server Toolbox (C360) the harness reports bare tool names under its
    `managed_tools` label (the agent's only tool source is the Toolbox), so the
    backend is inferred from the (disjoint) tool sets.
    """
    server, _, tool = (name or "").rpartition("___")
    tool = tool or name or ""
    # Names may be `<agent toolbox label>___<server>___<tool>` (e.g. c360_toolbox___salesforce_mcp___whoami).
    if "salesforce" in server:
        system = "salesforce"
    elif "servicenow" in server:
        system = "servicenow"
    elif server or (server_label and ("toolbox" in server_label or server_label == "managed_tools")):
        system = "salesforce" if tool in SF_TOOLS else "servicenow" if tool in SN_TOOLS else "unknown"
    else:
        system = "unknown"
    return system, tool


def run_turn(client, conv, text, scenario, log):
    """Send one user turn; loop through tool approvals. Returns (final_text, items, gate)."""
    items, request = [], text
    for _ in range(25):
        t0 = time.monotonic()
        resp = None
        for attempt in range(4):
            try:
                resp = client.responses.create(input=request, conversation=conv, timeout=900)
                break
            except Exception as e:  # noqa: BLE001
                if "429" in str(e) and attempt < 3:
                    time.sleep(30 * (attempt + 1))
                    continue
                raise
        out = [o.model_dump() if hasattr(o, "model_dump") else dict(o) for o in (resp.output or [])]
        items += out
        log.append({"elapsed_s": round(time.monotonic() - t0, 1), "status": resp.status,
                    "types": [o.get("type") for o in out],
                    "usage": (resp.usage.model_dump() if getattr(resp, "usage", None) else None)})
        for o in out:
            if o.get("type") == "oauth_consent_request":
                return "", items, f"oauth_consent_request ({o.get('server_label')})"
        approvals = [o for o in out if o.get("type") == "mcp_approval_request"]
        if not approvals:
            return resp.output_text or "", items, "none"
        request = []
        for a in approvals:
            _, tool = tool_parts(a.get("name"), a.get("server_label"))
            ok = tool not in WRITE_TOOLS or scenario["approve_writes"]
            a["_decision"] = "approve" if ok else "deny"
            request.append({"type": "mcp_approval_response", "approval_request_id": a["id"], "approve": ok})
    return "", items, "approval_loop_limit"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True, choices=["salesforce", "servicenow", "customer360"])
    p.add_argument("--identity", default="ozgur", help="substring of the signed-in user's backend username")
    p.add_argument("--only", help="comma-separated scenario numbers")
    p.add_argument("--out", help="write sanitized JSON results here")
    args = p.parse_args()

    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential

    agent, scenarios = profiles(args.identity.lower())[args.profile]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        scenarios = [s for s in scenarios if s["label"].split(".")[0] in wanted]
    print(f"Agent: {agent}  tag: {TAG}  scenarios: {len(scenarios)}", flush=True)

    results, conversations = [], []
    with AIProjectClient(endpoint=PROJECT, credential=AzureCliCredential(), allow_preview=True) as project:
        with project.get_openai_client(agent_name=agent, max_retries=0, timeout=900,
                                       default_headers={"Foundry-Features": "MicrosoftCopilot=V1Preview"}) as client:
            try:
                for s in scenarios:
                    conv = client.conversations.create().id
                    conversations.append(conv)
                    print(f"\n=== {s['label']}", flush=True)
                    log, items, texts, gates = [], [], [], []
                    try:
                        for turn in s["turns"]:
                            text, turn_items, gate = run_turn(client, conv, turn, s, log)
                            items += turn_items
                            texts.append(text)
                            gates.append(gate)
                            print(f"  > {turn[:90]}\n  < [{gate}] {text[:400].replace(chr(10), ' ')}", flush=True)
                            if gate != "none":
                                break
                    except Exception as e:  # noqa: BLE001
                        gates.append(f"exception: {type(e).__name__}: {str(e)[:300]}")
                    calls = [o for o in items if o.get("type") == "mcp_call"]
                    parts = [tool_parts(c.get("name"), c.get("server_label")) for c in calls]
                    labels = sorted({str(c.get("server_label")) for c in calls})
                    tools = {t for _, t in parts}
                    systems = {sy for sy, _ in parts}
                    outputs = " ".join(str(c.get("output") or "") for c in calls).lower()
                    errors = [str(c.get("error"))[:300] for c in calls if c.get("error")]
                    denied = [a.get("name") for a in items if a.get("type") == "mcp_approval_request" and a.get("_decision") == "deny"]
                    problems = [g for g in gates if g != "none"]
                    if s["expect_tools"] - tools:
                        problems.append(f"missing tools {sorted(s['expect_tools'] - tools)}")
                    if s["expect_systems"] - systems:
                        problems.append(f"missing systems {sorted(s['expect_systems'] - systems)}")
                    if "unknown" in systems:
                        problems.append("tool call not via Toolbox namespace")
                    if not s["approve_writes"] and (tools & s["forbid_tools"]):
                        problems.append(f"write executed without confirmation: {sorted(tools & s['forbid_tools'])}")
                    if errors:
                        problems.append(f"tool errors: {errors}")
                    final = (texts[-1] if texts else "").lower()
                    if s["expect_text_any"] and not any(x.lower() in final for x in s["expect_text_any"]):
                        problems.append(f"answer lacks any of {s['expect_text_any']}")
                    if s["expect_output_any"] and not any(x in outputs for x in s["expect_output_any"]):
                        problems.append(f"identity {s['expect_output_any']} not in raw tool output")
                    if s["label"].startswith("4.") and args.profile == "salesforce":
                        soql = [str(c.get("output") or "") for c in calls if c.get("name", "").endswith("soql_query")]
                        zero = r"""(expr0|totalsize|count)[\\"']*\s*[:=]\s*0\b"""
                        if not soql or not re.search(zero, soql[-1].lower()):
                            problems.append("cleanup not proven (final Task count != 0)")
                    status = "PASS" if not problems else "FAIL"
                    print(f"  labels={labels} tools={sorted(f'{sy}:{t}' for sy, t in parts)} denied={denied} -> {status} {problems}", flush=True)
                    results.append({"scenario": s["label"], "status": status, "problems": problems, "server_labels": labels,
                                    "tool_calls": [f"{sy}:{t}" for sy, t in parts],
                                    "raw_tool_names": sorted({str(c.get("name")) for c in calls}), "denied_writes": denied,
                                    "approvals": sum(1 for a in items if a.get("type") == "mcp_approval_request"),
                                    "turns": log, "final_answer_excerpt": (texts[-1] if texts else "")[:600]})
            finally:
                deleted = 0
                for conv in conversations:
                    try:
                        deleted += bool(client.conversations.delete(conv).deleted)
                    except Exception:  # noqa: BLE001
                        pass
                print(f"\nConversations deleted: {deleted}/{len(conversations)}")

    passed = sum(r["status"] == "PASS" for r in results)
    print(f"\n{agent}: {passed}/{len(results)} PASS")
    if args.out:
        Path(args.out).write_text(json.dumps({"agent": agent, "tag": TAG, "passed": passed, "total": len(results),
                                              "conversations_deleted": deleted, "results": results}, indent=2),
                                  encoding="utf-8")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
