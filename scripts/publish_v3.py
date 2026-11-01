#!/usr/bin/env python3
"""Publish the V3 managed-harness agents to Microsoft 365 / Teams through their agent endpoint.

Agent Applications (the legacy publish path) cannot serve harness agents, so V3 is
published per agent (Learn: "Publish agents to Microsoft 365 Copilot and Teams",
REST steps 1, 2 and 4):

  1. pin ``agent_endpoint.version_selector`` to the E2E-validated version;
  2. the Azure Bot (``infra/modules/bot-service-v3.bicep``) already points at the
     agent's Activity Protocol endpoint with ``msaAppId`` = the agent instance identity;
  3. enable the Activity protocol and add the ``BotServiceRbac`` authorization scheme
     next to the existing ``Entra`` scheme (kept byte-for-byte);
  4. ``POST /agents/<agent>/microsoft365/publish`` (scope ``Shared``).

Every step is idempotent: it reads the agent first and only PATCHes/POSTs what differs,
and a "version already exists" publish response counts as already published.

Usage:
    python scripts/publish_v3.py --dry-run          # show the plan only
    python scripts/publish_v3.py                    # apply to both agents
    python scripts/publish_v3.py --only salesforce-assistant-v3
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

PROJECT = "https://aoai-sf-mcp-obo.services.ai.azure.com/api/projects/aiproj-sf-mcp-obo"
SUBSCRIPTION = "44026b8b-9f88-44d9-8f46-0898baa4bcd5"
RESOURCE_GROUP = "rg-sf-mcp-obo"
API = "api-version=v1"
APP_VERSION = "1.0.0"
REPO_URL = "https://github.com/ozgurkarahan/salesforce-meta-tool-identity-propagation"


@dataclass(frozen=True)
class AgentSpec:
    agent: str
    version: str  # exact E2E-validated agent version served by the endpoint
    bot: str
    display_name: str
    short_description: str
    full_description: str


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        agent="salesforce-assistant-v3",
        version="2",
        bot="bot-salesforce-assistant-v3",
        display_name="Salesforce Assistant (V3)",
        short_description="Salesforce CRM assistant acting as you.",
        full_description=(
            "Queries and updates Salesforce CRM with your own identity (per-user OAuth). "
            "Every write is shown to you first and only runs after you confirm."
        ),
    ),
    AgentSpec(
        agent="customer360-assistant-v3",
        version="4",
        bot="bot-customer360-assistant-v3",
        display_name="Customer 360 Assistant (V3)",
        short_description="Salesforce + ServiceNow customer 360 view.",
        full_description=(
            "Correlates Salesforce accounts and opportunities with ServiceNow incidents and "
            "changes using your own identity. Every write needs your explicit confirmation."
        ),
    ),
)

BOT_SERVICE_RBAC = {"type": "BotServiceRbac"}


def bot_arm_id(bot: str) -> str:
    return (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.BotService/botServices/{bot}"
    )


def endpoint_of(agent: dict) -> dict:
    return agent.get("agent_endpoint") or {}


def pin_patch(agent: dict, version: str) -> dict | None:
    """Merge-patch pinning 100% traffic to ``version``; None when already pinned."""
    want = [{"type": "FixedRatio", "agent_version": version, "traffic_percentage": 100}]
    current = (endpoint_of(agent).get("version_selector") or {}).get("version_selection_rules") or []
    normalised = [
        {
            "type": s.get("type"),
            "agent_version": str(s.get("agent_version")),
            "traffic_percentage": s.get("traffic_percentage"),
        }
        for s in current
    ]
    if normalised == want:
        return None
    return {"agent_endpoint": {"version_selector": {"version_selection_rules": want}}}


def endpoint_patch(agent: dict) -> dict | None:
    """Merge-patch adding the Activity protocol and BotServiceRbac; None when present.

    ``protocol_configuration`` and ``authorization_schemes`` are replaced wholesale by
    the service, so the existing protocols and schemes (the ``Entra`` one in
    particular) are carried over unchanged.
    """
    ep = endpoint_of(agent)
    protocols = copy.deepcopy(ep.get("protocol_configuration") or {})
    schemes = copy.deepcopy(ep.get("authorization_schemes") or [])
    if not any(s.get("type") == "Entra" for s in schemes):
        raise ValueError(f"{agent.get('name')}: no Entra authorization scheme to preserve")
    changed = False
    for proto in ("responses", "activity"):
        if proto not in protocols:
            protocols[proto] = {}
            changed = True
    if not any(s.get("type") == "BotServiceRbac" for s in schemes):
        schemes.append(dict(BOT_SERVICE_RBAC))
        changed = True
    if not changed:
        return None
    return {
        "agent_endpoint": {
            "protocol_configuration": protocols,
            "authorization_schemes": schemes,
        }
    }


def publish_body(spec: AgentSpec) -> dict:
    return {
        "agentDisplayName": spec.display_name,
        "botServiceArmId": bot_arm_id(spec.bot),
        "publishScope": "Shared",
        "publishAsAutopilot": False,
        "appVersion": APP_VERSION,
        "shortDescription": spec.short_description,
        "fullDescription": spec.full_description,
        "developerName": "Ozgur Karahan",
        "developerWebsiteUrl": REPO_URL,
        "privacyUrl": f"{REPO_URL}#readme",
        "termsOfUseUrl": f"{REPO_URL}#readme",
    }


def is_already_published(status: int, body: str) -> bool:
    return status in (400, 409) and "already exist" in body.lower()


# --------------------------------------------------------------------------- HTTP

Http = Callable[[str, str, dict | None, str], tuple[int, str]]


def make_http(token: str) -> Http:
    def http(method: str, url: str, body: dict | None, content_type: str) -> tuple[int, str]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")

    return http


def _ok(status: int) -> bool:
    return 200 <= status < 300


def publish_agent(spec: AgentSpec, http: Http, dry_run: bool = False, publish: bool = True) -> dict:
    """Run steps 1, 3 and 4 for one agent. Returns a summary dict."""
    url = f"{PROJECT}/agents/{spec.agent}?{API}"
    summary: dict = {"agent": spec.agent, "version": spec.version, "bot": bot_arm_id(spec.bot)}

    status, text = http("GET", url, None, "")
    if not _ok(status):
        raise RuntimeError(f"GET {spec.agent} -> {status}: {text[:300]}")
    agent = json.loads(text)

    for step, build in (
        ("pin", lambda a: pin_patch(a, spec.version)),
        ("endpoint", endpoint_patch),
    ):
        patch = build(agent)
        if patch is None:
            summary[step] = "unchanged"
            continue
        summary[step] = "planned" if dry_run else "patched"
        if dry_run:
            summary[f"{step}_body"] = patch
            continue
        status, text = http("PATCH", url, patch, "application/merge-patch+json")
        if not _ok(status):
            raise RuntimeError(f"PATCH {step} {spec.agent} -> {status}: {text[:300]}")
        status, text = http("GET", url, None, "")
        if not _ok(status):
            raise RuntimeError(f"GET {spec.agent} -> {status}: {text[:300]}")
        agent = json.loads(text)

    if not publish:
        summary["publish"] = "skipped"
        return summary
    if dry_run:
        summary["publish"] = "planned"
        return summary

    status, text = http(
        "POST",
        f"{PROJECT}/agents/{spec.agent}/microsoft365/publish?{API}",
        publish_body(spec),
        "application/json",
    )
    if _ok(status):
        result = json.loads(text) if text.strip() else {}
        summary["publish"] = "published"
        summary["titleId"] = result.get("titleId")
    elif is_already_published(status, text):
        summary["publish"] = f"already published (appVersion {APP_VERSION})"
    else:
        raise RuntimeError(f"publish {spec.agent} -> {status}: {text[:500]}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    parser.add_argument("--only", choices=[a.agent for a in AGENTS])
    parser.add_argument("--no-publish", action="store_true",
                        help="only pin + enable the Activity endpoint (steps 1 and 3)")
    args = parser.parse_args(argv)

    from azure.identity import AzureCliCredential

    token = AzureCliCredential().get_token("https://ai.azure.com/.default").token
    http = make_http(token)
    for spec in AGENTS:
        if args.only and spec.agent != args.only:
            continue
        summary = publish_agent(spec, http, dry_run=args.dry_run, publish=not args.no_publish)
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
