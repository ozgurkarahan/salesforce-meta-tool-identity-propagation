"""End-to-end test: Customer 360 agent via Foundry Responses API.

Sends progressive cross-system queries through the Customer 360 agent
and validates that both salesforce_mcp and servicenow_mcp tools are called.
Reports per-turn token usage and cross-system validation summary.

Usage:
    python scripts/test_e2e_customer360.py
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


def handle_approval(openai_client, response, agent_name):
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
    response = call_with_retry(lambda: openai_client.responses.create(
        previous_response_id=response.id,
        input=approval_input,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    ))
    elapsed = time.monotonic() - t0

    output_items = getattr(response, "output", [])
    output_types = [getattr(item, "type", "unknown") for item in output_items]
    print(f"  Output types: {output_types}")
    dump_output_items(output_items)
    print_usage("after approval", response, elapsed)

    return handle_approval(openai_client, response, agent_name)


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


def get_servers_called(output_items):
    """Extract set of MCP server labels called in the response."""
    servers = set()
    for item in output_items:
        if getattr(item, "type", "") == "mcp_call":
            server = getattr(item, "server_label", "")
            if server:
                servers.add(server)
    return servers


def main():
    print("=" * 70)
    print("  E2E Test: Customer 360 Agent (SF + SN)")
    print("=" * 70)
    print()

    load_azd_env()

    project_endpoint = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
    if not project_endpoint:
        print("ERROR: AI_FOUNDRY_PROJECT_ENDPOINT not set")
        sys.exit(1)

    try:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
    except ImportError:
        print("ERROR: pip install azure-ai-projects azure-identity")
        sys.exit(1)

    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=project_endpoint, credential=credential)
    openai_client = client.get_openai_client()

    # Find agent
    agent_name = "customer360-assistant"
    agents = list(client.agents.list())
    agent = None
    for a in agents:
        name = getattr(a, "name", "")
        if name == agent_name or name.startswith(f"{agent_name}-"):
            agent = a
            break

    if not agent:
        names = [getattr(a, "name", "?") for a in agents]
        print(f"ERROR: {agent_name} not found (agents: {names})")
        sys.exit(1)

    actual_name = getattr(agent, "name", agent_name)
    print(f"Agent:    {actual_name}")
    print(f"Endpoint: {project_endpoint}")
    print()

    # Progressive demo scenarios — multi-turn conversation
    scenarios = [
        {
            "label": "1. Unified lookup",
            "query": "Tell me everything about Contoso Ltd — pull their account details, "
                     "contacts, open opportunities, and cases from Salesforce, plus any "
                     "incidents and change requests from ServiceNow.",
            "expect_tools": {"salesforce_mcp", "servicenow_mcp"},
            "demonstrates": "Multi-tool orchestration, data correlation",
        },
        {
            "label": "2. Cross-system correlation",
            "query": "Acme Corp reports API gateway outages — what's the full picture? "
                     "Check both their Salesforce cases and ServiceNow incidents.",
            "expect_tools": {"salesforce_mcp", "servicenow_mcp"},
            "demonstrates": "SF Cases to SN Incidents correlation",
        },
        {
            "label": "3. Meeting prep",
            "query": "Prepare me for my call with Fabrikam Inc tomorrow. "
                     "Pull their account details, any open opportunities, and active incidents.",
            "expect_tools": {"salesforce_mcp", "servicenow_mcp"},
            "demonstrates": "Account summary + open opps + active incidents",
        },
        {
            "label": "4. Proactive insights",
            "query": "Which of our strategic accounts (by revenue) have the most "
                     "open incidents in ServiceNow? Show revenue at risk.",
            "expect_tools": {"salesforce_mcp", "servicenow_mcp"},
            "demonstrates": "Cross-system analytics, prioritization",
        },
        {
            "label": "5. Cross-system actions (read-only)",
            "query": "Show me the P1 incidents from ServiceNow that are currently unresolved. "
                     "For the most critical one, check Salesforce to see if there's already "
                     "a case. If not, describe what a new escalation case would look like.",
            "expect_tools": {"salesforce_mcp", "servicenow_mcp"},
            "demonstrates": "Write operations across both systems (describe only)",
        },
        {
            "label": "6. Escalation workflow",
            "query": "Show me all P1 and P2 incidents from ServiceNow, then for each one "
                     "identify which Salesforce accounts are affected and their total "
                     "pipeline value. Rank by revenue at risk.",
            "expect_tools": {"salesforce_mcp", "servicenow_mcp"},
            "demonstrates": "Multi-system read + action orchestration",
        },
    ]

    # Create conversation
    conversation = openai_client.conversations.create()
    print(f"Conversation: {conversation.id}")
    print()

    all_usage = []
    all_validation = []
    prev_response_id = None
    prev_elapsed = 0.0

    for i, scenario in enumerate(scenarios):
        # Token-aware pacing — heavier turns get more cooldown
        if i > 0:
            last = all_usage[-1] if all_usage else None
            delay = smart_delay(last, prev_elapsed)
            print(f"\n  (pacing {delay}s before next turn...)")
            time.sleep(delay)

        print(f"\n{'='*70}")
        print(f"  {scenario['label']}")
        print(f"  Query: {scenario['query'][:100]}...")
        print(f"  Demonstrates: {scenario['demonstrates']}")
        print(f"{'='*70}")

        t0 = time.monotonic()
        all_servers = set()
        try:
            kwargs = {
                "input": scenario["query"],
                "extra_body": {"agent_reference": {"name": actual_name, "type": "agent_reference"}},
            }
            if prev_response_id:
                kwargs["previous_response_id"] = prev_response_id
            else:
                kwargs["conversation"] = conversation.id

            response = call_with_retry(lambda: openai_client.responses.create(**kwargs))
            elapsed = time.monotonic() - t0

            output_items = getattr(response, "output", [])
            output_types = [getattr(item, "type", "unknown") for item in output_items]
            print(f"\n  Response ID: {response.id}")
            print(f"  Output types: {output_types}")
            dump_output_items(output_items)
            all_servers.update(get_servers_called(output_items))

            # Handle OAuth consent (first time)
            consent_items = [
                item for item in output_items
                if getattr(item, "type", "") == "oauth_consent_request"
            ]
            if consent_items:
                consent_link = getattr(consent_items[0], "consent_link", "")
                print(f"\n  OAuth consent required: {consent_link[:100]}")
                import webbrowser
                try:
                    webbrowser.open(consent_link)
                    print("  (Opening in browser...)")
                except Exception:
                    pass
                input("\n  Press ENTER after completing authentication...")

                t0 = time.monotonic()
                response = call_with_retry(lambda: openai_client.responses.create(
                    previous_response_id=response.id,
                    input=scenario["query"],
                    extra_body={"agent_reference": {"name": actual_name, "type": "agent_reference"}},
                ))
                elapsed = time.monotonic() - t0
                output_items = getattr(response, "output", [])
                dump_output_items(output_items)
                all_servers.update(get_servers_called(output_items))

            # Handle MCP approvals (429-protected)
            response = handle_approval(openai_client, response, actual_name)
            output_items = getattr(response, "output", [])
            all_servers.update(get_servers_called(output_items))

            usage = print_usage(scenario["label"], response, elapsed)

            # Count tool calls for metrics
            tool_calls = len([it for it in output_items if getattr(it, "type", "") == "mcp_call"])

            # Validate expected tools
            expected = scenario.get("expect_tools", set())
            if expected and not expected.issubset(all_servers):
                missing = expected - all_servers
                print(f"\n  WARN: Expected tools from {missing} but only got {all_servers}")
                all_validation.append(False)
            else:
                print(f"\n  OK: Called tools from {all_servers}")
                all_validation.append(True)

            servers_str = "+".join(sorted(s.replace("_mcp", "").upper()[:2] for s in all_servers)) or "none"
            if usage:
                all_usage.append({
                    "turn": i + 1,
                    "label": scenario["label"],
                    "servers": servers_str,
                    "elapsed": round(elapsed, 1),
                    "tools": tool_calls,
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

    # Summary
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
        total_elapsed = sum(u["elapsed"] for u in all_usage)
        total_tools = sum(u["tools"] for u in all_usage)
        print(
            f"  {'TOTAL':<6} {sum(u['input'] for u in all_usage):>8,} "
            f"{sum(u['output'] for u in all_usage):>8,} "
            f"{sum(u['total'] for u in all_usage):>8,} "
            f"{'':>8} {total_elapsed:>5.1f}s {total_tools:>5}"
        )

    passed = sum(all_validation)
    total = len(all_validation)
    mark = "pass" if passed == total else "FAIL"
    print(f"\n  Cross-system validation: {passed}/{total} turns called expected tools ({mark})")
    print()


if __name__ == "__main__":
    main()
