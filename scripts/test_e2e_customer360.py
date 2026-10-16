"""End-to-end test: Customer 360 agent via Foundry Responses API.

Sends progressive cross-system queries (one or more per skill) through the
Customer 360 agent, attributes every MCP call to Salesforce or ServiceNow
(direct tools or toolbox "<server>___<tool>" names), checks per-skill behaviour
(including no write without user approval) and reports per-turn token usage.
Exits non-zero when any check fails.

Usage:
    python scripts/test_e2e_customer360.py                      # latest version
    python scripts/test_e2e_customer360.py --path version --version 23   # pre-route
    python scripts/test_e2e_customer360.py --path app           # routed application
    python scripts/test_e2e_customer360.py --only 4,7
"""

import io
import json
import os
import subprocess
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Force UTF-8 stdout on Windows to handle Unicode in agent responses
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def load_azd_env():
    result = subprocess.run(
        "azd env get-values", capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if "=" in line:
            key, _, value = line.partition("=")
            value = value.strip('"').strip("'")
            os.environ.setdefault(key, value)


def dump_output_items(output_items):
    """Print output items summary."""
    for item in output_items:
        item_type = getattr(item, "type", "unknown")
        if item_type == "message":
            content = getattr(item, "content", [])
            for c in content:
                if hasattr(c, "text"):
                    text = c.text
                    print(f"  [message] {text[:300]}{'...' if len(text) > 300 else ''}")
        elif item_type == "mcp_call":
            name = getattr(item, "name", "?")
            server = getattr(item, "server_label", "?")
            args = getattr(item, "arguments", "")
            print(f"  [mcp_call] {server}.{name}({args[:150]})")
        elif item_type == "mcp_approval_request":
            name = getattr(item, "name", "?")
            print(f"  [mcp_approval] {name}")
        elif item_type == "oauth_consent_request":
            print(f"  [oauth_consent] {getattr(item, 'consent_link', '')[:80]}")
        else:
            print(f"  [{item_type}] {str(item)[:200]}")


def print_usage(label, response, elapsed):
    """Print token usage from response. Returns usage dict."""
    usage = getattr(response, "usage", None)
    if usage:
        input_t = getattr(usage, "input_tokens", 0)
        output_t = getattr(usage, "output_tokens", 0)
        total_t = getattr(usage, "total_tokens", 0)
        details = getattr(usage, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details else 0
        print(f"\n  TOKEN USAGE ({label}):")
        print(f"    Input tokens:  {input_t:,}")
        print(f"    Output tokens: {output_t:,}")
        print(f"    Total tokens:  {total_t:,}")
        if cached:
            print(f"    Cached tokens: {cached:,}")
        print(f"    Elapsed:       {elapsed:.1f}s")
        return {"input": input_t, "output": output_t, "total": total_t, "cached": cached}
    else:
        print(f"\n  TOKEN USAGE ({label}): not available")
        print(f"    Elapsed: {elapsed:.1f}s")
        return None


def call_with_retry(func, max_retries=3, base_wait=30):
    """Call func() with 429 retry + exponential backoff. Works for any responses.create() call."""
    for retry in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            if "429" in str(e) and retry < max_retries:
                wait = base_wait * (retry + 1)
                print(f"\n  429 rate limit — waiting {wait}s (retry {retry + 1}/{max_retries})...")
                time.sleep(wait)
            else:
                raise


def handle_approval(target, response, on_response=None):
    """Auto-approve MCP tool calls if needed (recursive). Protected by 429 retry."""
    output_items = getattr(response, "output", [])
    approval_items = [
        item for item in output_items
        if getattr(item, "type", "") == "mcp_approval_request"
    ]
    if not approval_items:
        return response

    print(f"\n  Auto-approving {len(approval_items)} MCP tool call(s)...")
    for item in approval_items:
        print(f"    Approving: {getattr(item, 'name', '?')}")

    approval_input = [
        {
            "type": "mcp_approval_response",
            "approve": True,
            "approval_request_id": item.id,
        }
        for item in approval_items
    ]

    t0 = time.monotonic()
    response = call_with_retry(lambda: target.create(
        previous_response_id=response.id, input=approval_input))
    elapsed = time.monotonic() - t0

    output_items = getattr(response, "output", [])
    output_types = [getattr(item, "type", "unknown") for item in output_items]
    print(f"  Output types: {output_types}")
    dump_output_items(output_items)
    print_usage("after approval", response, elapsed)
    if on_response:
        on_response(response)

    return handle_approval(target, response, on_response)


def smart_delay(prev_usage, prev_elapsed, base=10):
    """Token-aware inter-turn delay. Heavier turns get more cooldown."""
    if not prev_usage:
        return base
    total = prev_usage.get("total", 0)
    # Estimate TPM consumed this turn, target staying under 150K TPM
    tpm = (total / max(prev_elapsed, 1)) * 60
    if tpm > 90_000:
        return base + int((tpm / 90_000 - 1) * base)
    return base


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PROTOCOL_API = "2025-11-15-preview"
WRITE_TOOLS = {"write_record", "process_approval", "write"}
BOTH = {"salesforce", "servicenow"}

SCENARIOS = [
    {
        "label": "1. Unified lookup",
        "skill": "customer360-correlation",
        "query": "Tell me everything about Contoso Ltd — pull their account details, "
                 "contacts, open opportunities, and cases from Salesforce, plus any "
                 "incidents and change requests from ServiceNow.",
        "expect_systems": BOTH,
    },
    {
        "label": "2. Cross-system correlation",
        "skill": "customer360-correlation",
        "query": "Acme Corp reports API gateway outages — what's the full picture? "
                 "Check both their Salesforce cases and ServiceNow incidents.",
        "expect_systems": BOTH,
    },
    {
        "label": "3. Meeting prep",
        "skill": "meeting-prep",
        "query": "Prepare me for my call with Fabrikam Inc tomorrow. "
                 "Pull their account details, any open opportunities, and active incidents.",
        "expect_systems": BOTH,
    },
    {
        "label": "4. Revenue at risk",
        "skill": "revenue-at-risk",
        "query": "Show me all P1 and P2 incidents from ServiceNow, then for each one "
                 "identify which Salesforce accounts are affected and their total "
                 "pipeline value. Rank by revenue at risk.",
        "expect_systems": BOTH,
        "expect_text_any": ["$", "USD", "€", "EUR"],
    },
    {
        "label": "5. Change risk",
        "skill": "change-risk",
        "query": "Which upcoming ServiceNow change requests could affect Salesforce "
                 "accounts with open opportunities? Assess the risk for each.",
        "expect_systems": BOTH,
    },
    {
        "label": "6. Escalation (read-only)",
        "skill": "salesforce-crm + servicenow-itsm",
        "query": "Show me the P1 incidents from ServiceNow that are currently unresolved. "
                 "For the most critical one, check Salesforce to see if there's already "
                 "a case. If not, describe what a new escalation case would look like.",
        "expect_systems": BOTH,
        "forbid_tools": WRITE_TOOLS,
    },
    {
        "label": "7. Write guard",
        "skill": "salesforce-crm",
        "query": "Change the Industry of Acme Corp to Retail.",
        "expect_systems": set(),
        "forbid_tools": WRITE_TOOLS,
        "expect_text_any": ["confirm", "proceed", "?"],
    },
]


def wait_for_consent():
    """ENTER on a terminal; with CONSENT_FLAG_FILE set, wait for that file instead
    (lets a background run continue after the user consents)."""
    flag = os.environ.get("CONSENT_FLAG_FILE", "")
    if not flag:
        input("\n  Press ENTER after completing authentication...")
        return
    print(f"  Waiting for {flag} (create it after consenting)...", flush=True)
    while not os.path.exists(flag):
        time.sleep(5)
    os.remove(flag)


def get_calls(output_items):
    """[(system, tool)] for every mcp_call; works for direct MCP tools and toolbox names."""
    from foundry_helpers import tool_system
    return [tool_system(getattr(it, "server_label", ""), getattr(it, "name", ""))
            for it in output_items if getattr(it, "type", "") == "mcp_call"]


class Target:
    """Where E2E requests go: agent_reference (latest), an exact version, or the
    published Agent Application endpoint (what Teams traffic is routed to)."""

    def __init__(self, path, agent, version, app, project_endpoint, credential, project_client):
        self.path = path
        self.ref = None
        if path == "app":
            from openai import OpenAI
            token = credential.get_token("https://ai.azure.com/.default").token
            base = f"{project_endpoint.rstrip('/')}/applications/{app}/protocols/openai"
            self.client = OpenAI(base_url=base, api_key=token,
                                 default_query={"api-version": APP_PROTOCOL_API})
            self.description = f"application '{app}' (routed deployment)"
        else:
            self.client = project_client.get_openai_client()
            self.ref = {"name": agent, "type": "agent_reference"}
            if path == "version":
                self.ref["version"] = str(version)
                self.description = f"agent '{agent}' v{version} (pre-route)"
            else:
                self.description = f"agent '{agent}' (agent_reference, latest)"

    def create(self, **kwargs):
        if self.path == "app":
            # Application-scoped Responses are stateless: no previous_response_id.
            kwargs.pop("previous_response_id", None)
        if self.ref:
            kwargs["extra_body"] = {"agent_reference": self.ref}
        return self.client.responses.create(**kwargs)


def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Customer 360 E2E (v3)")
    p.add_argument("--agent", default="customer360-assistant")
    p.add_argument("--app", help="Agent Application name for --path app (default: --agent)")
    p.add_argument("--path", choices=["reference", "version", "app"], default="reference",
                   help="reference = latest agent version; version = exact --version "
                        "(pre-route); app = published application endpoint (post-route)")
    p.add_argument("--version", help="agent version for --path version "
                                     "(default: C360_AGENT_VERSION from azd env)")
    p.add_argument("--only", help="comma-separated scenario numbers, e.g. 4,7")
    return p.parse_args()


def main():
    args = parse_args()
    print("=" * 70)
    print("  E2E Test: Customer 360 Agent (SF + SN) — v3")
    print("=" * 70)
    print()

    load_azd_env()
    sys.path.insert(0, os.path.join(REPO_ROOT, "src", "shared"))

    project_endpoint = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
    if not project_endpoint:
        print("ERROR: AI_FOUNDRY_PROJECT_ENDPOINT not set")
        sys.exit(1)

    try:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
    except ImportError:
        print("ERROR: pip install -r requirements.txt")
        sys.exit(1)

    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=project_endpoint, credential=credential)
    version = args.version or os.environ.get("C360_AGENT_VERSION", "")
    if args.path == "version" and not version:
        version = client.agents.get(args.agent).as_dict()["versions"]["latest"]["version"]
    target = Target(args.path, args.agent, version, args.app or args.agent,
                    project_endpoint, credential, client)
    print(f"Target:   {target.description}")
    print(f"Endpoint: {project_endpoint}")

    scenarios = SCENARIOS
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        scenarios = [s for s in SCENARIOS if s["label"].split(".")[0] in wanted]

    all_usage = []
    all_validation = []
    prev_response_id = None
    prev_elapsed = 0.0

    for i, scenario in enumerate(scenarios):
        if i > 0:
            last = all_usage[-1] if all_usage else None
            delay = smart_delay(last, prev_elapsed)
            print(f"\n  (pacing {delay}s before next turn...)")
            time.sleep(delay)

        print(f"\n{'='*70}")
        print(f"  {scenario['label']}  [skill: {scenario['skill']}]")
        print(f"  Query: {scenario['query'][:100]}...")
        print(f"{'='*70}")

        t0 = time.monotonic()
        calls = []
        try:
            kwargs = {"input": scenario["query"]}
            if prev_response_id:
                kwargs["previous_response_id"] = prev_response_id
            response = call_with_retry(lambda: target.create(**kwargs))
            elapsed = time.monotonic() - t0

            output_items = getattr(response, "output", [])
            print(f"\n  Response ID: {response.id}")
            print(f"  Output types: {[getattr(it, 'type', 'unknown') for it in output_items]}")
            dump_output_items(output_items)
            calls += get_calls(output_items)

            # OAuth consent — one per connection (SF and SN may arrive in turn)
            for _ in range(3):
                consent_items = [it for it in output_items
                                 if getattr(it, "type", "") == "oauth_consent_request"]
                if not consent_items:
                    break
                consent_link = getattr(consent_items[0], "consent_link", "")
                label = getattr(consent_items[0], "server_label", "")
                print(f"\n  OAuth consent required ({label}). Open as the SAME user:\n  {consent_link}",
                      flush=True)
                if not os.environ.get("CONSENT_FLAG_FILE"):
                    import webbrowser
                    try:
                        webbrowser.open(consent_link)
                        print("  (Opening in browser...)")
                    except Exception:
                        pass
                wait_for_consent()

                t0 = time.monotonic()
                response = call_with_retry(lambda: target.create(
                    previous_response_id=response.id, input=scenario["query"]))
                elapsed = time.monotonic() - t0
                output_items = getattr(response, "output", [])
                dump_output_items(output_items)
                calls += get_calls(output_items)

            response = handle_approval(
                target, response, on_response=lambda r: calls.extend(
                    get_calls(getattr(r, "output", []))))
            output_items = getattr(response, "output", [])

            usage = print_usage(scenario["label"], response, elapsed)
            text = getattr(response, "output_text", "") or ""

            systems = {s for s, _ in calls}
            problems = []
            missing = scenario.get("expect_systems", set()) - systems
            if missing:
                problems.append(f"no call to {sorted(missing)}")
            if "unknown" in systems:
                problems.append("unattributed tool call (unknown system)")
            writes = [f"{s}:{t}" for s, t in calls if t in scenario.get("forbid_tools", set())]
            if writes:
                problems.append(f"write without approval: {writes}")
            expect_text = scenario.get("expect_text_any")
            if expect_text and not any(x.lower() in text.lower() for x in expect_text):
                problems.append(f"answer lacks any of {expect_text}")

            if problems:
                print(f"\n  FAIL: {'; '.join(problems)}  (calls: {calls})")
                all_validation.append(False)
            else:
                print(f"\n  OK: systems={sorted(systems) or ['none']} calls={len(calls)}")
                all_validation.append(True)

            servers_str = "+".join(sorted(s[:2].upper() for s in systems)) or "none"
            if usage:
                all_usage.append({
                    "turn": i + 1,
                    "label": scenario["label"],
                    "servers": servers_str,
                    "elapsed": round(elapsed, 1),
                    "tools": len(calls),
                    **usage,
                })

            prev_response_id = response.id
            prev_elapsed = elapsed

        except Exception as e:
            elapsed = time.monotonic() - t0
            prev_elapsed = elapsed
            err_msg = str(e).encode("ascii", errors="replace").decode()
            print(f"\n  ERROR ({elapsed:.1f}s): {err_msg}")
            all_validation.append(False)
            if hasattr(e, "response") and hasattr(e.response, "text"):
                print(f"  Response: {e.response.text[:500]}")

    print(f"\n\n{'='*70}")
    print("  SUMMARY: Token Usage Per Turn")
    print(f"{'='*70}")
    print(f"  {'Turn':<6} {'Input':>8} {'Output':>8} {'Total':>8} {'Cached':>8} {'Time':>6} {'Tools':>5}  {'Servers':<8} Label")
    print(f"  {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*5}  {'-'*8} {'-'*28}")
    for u in all_usage:
        print(
            f"  {u['turn']:<6} {u['input']:>8,} {u['output']:>8,} "
            f"{u['total']:>8,} {u['cached']:>8,} {u['elapsed']:>5.1f}s {u['tools']:>5}  "
            f"{u['servers']:<8} {u['label']}"
        )
    if all_usage:
        print(f"  {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*5}")
        print(
            f"  {'TOTAL':<6} {sum(u['input'] for u in all_usage):>8,} "
            f"{sum(u['output'] for u in all_usage):>8,} "
            f"{sum(u['total'] for u in all_usage):>8,} "
            f"{'':>8} {sum(u['elapsed'] for u in all_usage):>5.1f}s "
            f"{sum(u['tools'] for u in all_usage):>5}"
        )

    passed = sum(all_validation)
    total = len(all_validation)
    mark = "pass" if passed == total else "FAIL"
    print(f"\n  Behavioural checks: {passed}/{total} turns passed ({mark})")
    print(f"  Target: {target.description}")
    print()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
