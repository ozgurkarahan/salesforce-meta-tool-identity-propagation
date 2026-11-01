#!/usr/bin/env python3
"""Offline tests for scripts/publish_v3.py (no network; HTTP is faked)."""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import publish_v3 as pv  # noqa: E402

ENTRA = {"type": "Entra", "isolation_key_source": {"kind": "Entra"}}


def live_agent(name: str = "salesforce-assistant-v3") -> dict:
    """Shape of GET /agents/<name> before publishing (captured from the live project)."""
    return {
        "name": name,
        "agent_endpoint": {
            "version_selector": {"version_selection_rules": [
                {"type": "FixedRatio", "agent_version": "@latest", "traffic_percentage": 100}
            ]},
            "protocols": ["responses"],
            "protocol_configuration": {"responses": {}},
            "authorization_schemes": [copy.deepcopy(ENTRA)],
        },
    }


def merge_patch(target: dict, patch: dict) -> dict:
    out = copy.deepcopy(target)
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_patch(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class FakeService:
    def __init__(self, agent: dict, publish_status: int = 200, publish_text: str = ""):
        self.agent = agent
        self.calls: list[tuple[str, str, dict | None, str]] = []
        self.publish_status = publish_status
        self.publish_text = publish_text or json.dumps({"titleId": "T_123"})

    def __call__(self, method, url, body, content_type):
        self.calls.append((method, url, body, content_type))
        if method == "GET":
            return 200, json.dumps(self.agent)
        if method == "PATCH":
            self.agent = merge_patch(self.agent, body)
            return 200, json.dumps(self.agent)
        if method == "POST":
            return self.publish_status, self.publish_text
        raise AssertionError(method)

    def methods(self):
        return [c[0] for c in self.calls]


SF = pv.AGENTS[0]
C360 = pv.AGENTS[1]


class TestPlanBuilders(unittest.TestCase):
    def test_specs_match_validated_versions_and_bots(self):
        self.assertEqual(
            {(a.agent, a.version, a.bot, a.display_name) for a in pv.AGENTS},
            {
                ("salesforce-assistant-v3", "2", "bot-salesforce-assistant-v3", "Salesforce Assistant (V3)"),
                ("customer360-assistant-v3", "3", "bot-customer360-assistant-v3", "Customer 360 Assistant (V3)"),
            },
        )

    def test_pin_replaces_latest_with_exact_version(self):
        patch = pv.pin_patch(live_agent(), "2")
        self.assertEqual(
            patch,
            {"agent_endpoint": {"version_selector": {"version_selection_rules": [
                {"type": "FixedRatio", "agent_version": "2", "traffic_percentage": 100}]}}},
        )
        self.assertNotIn("protocol_configuration", patch["agent_endpoint"])

    def test_pin_is_noop_when_already_pinned(self):
        agent = merge_patch(live_agent(), pv.pin_patch(live_agent(), "2"))
        self.assertIsNone(pv.pin_patch(agent, "2"))
        self.assertIsNotNone(pv.pin_patch(agent, "3"))

    def test_endpoint_patch_keeps_entra_exactly_and_adds_rbac(self):
        patch = pv.endpoint_patch(live_agent())
        ep = patch["agent_endpoint"]
        self.assertEqual(ep["protocol_configuration"], {"responses": {}, "activity": {}})
        self.assertEqual(ep["authorization_schemes"], [ENTRA, {"type": "BotServiceRbac"}])
        self.assertNotIn("version_selector", ep)

    def test_endpoint_patch_is_noop_when_present(self):
        agent = merge_patch(live_agent(), pv.endpoint_patch(live_agent()))
        self.assertIsNone(pv.endpoint_patch(agent))

    def test_endpoint_patch_refuses_without_entra(self):
        agent = live_agent()
        agent["agent_endpoint"]["authorization_schemes"] = []
        with self.assertRaises(ValueError):
            pv.endpoint_patch(agent)

    def test_publish_body(self):
        body = pv.publish_body(C360)
        self.assertEqual(body["publishScope"], "Shared")
        self.assertIs(body["publishAsAutopilot"], False)
        self.assertEqual(body["appVersion"], "1.0.0")
        self.assertEqual(body["agentDisplayName"], "Customer 360 Assistant (V3)")
        self.assertEqual(
            body["botServiceArmId"],
            "/subscriptions/44026b8b-9f88-44d9-8f46-0898baa4bcd5/resourceGroups/rg-sf-mcp-obo"
            "/providers/Microsoft.BotService/botServices/bot-customer360-assistant-v3",
        )

    def test_already_published_detection(self):
        self.assertTrue(pv.is_already_published(409, '{"error":"App version already exists"}'))
        self.assertTrue(pv.is_already_published(400, "Version 1.0.0 already exists for title"))
        self.assertFalse(pv.is_already_published(400, "invalid botServiceArmId"))
        self.assertFalse(pv.is_already_published(500, "already exists"))


class TestPublishFlow(unittest.TestCase):
    def test_first_run_pins_patches_and_publishes(self):
        svc = FakeService(live_agent())
        out = pv.publish_agent(SF, svc)
        self.assertEqual(out["pin"], "patched")
        self.assertEqual(out["endpoint"], "patched")
        self.assertEqual(out["titleId"], "T_123")
        self.assertEqual(svc.methods(), ["GET", "PATCH", "GET", "PATCH", "GET", "POST"])
        for method, url, _, ctype in svc.calls:
            if method == "PATCH":
                self.assertEqual(ctype, "application/merge-patch+json")
                self.assertTrue(url.endswith("/agents/salesforce-assistant-v3?api-version=v1"))
        self.assertTrue(svc.calls[-1][1].endswith(
            "/agents/salesforce-assistant-v3/microsoft365/publish?api-version=v1"))
        ep = svc.agent["agent_endpoint"]
        self.assertEqual(ep["version_selector"]["version_selection_rules"][0]["agent_version"], "2")
        self.assertEqual(ep["authorization_schemes"][0], ENTRA)

    def test_second_run_is_idempotent(self):
        svc = FakeService(live_agent())
        pv.publish_agent(SF, svc)
        svc2 = FakeService(svc.agent, publish_status=409,
                           publish_text='{"error":{"message":"App version already exists"}}')
        out = pv.publish_agent(SF, svc2)
        self.assertEqual((out["pin"], out["endpoint"]), ("unchanged", "unchanged"))
        self.assertTrue(out["publish"].startswith("already published"))
        self.assertEqual(svc2.methods(), ["GET", "POST"])

    def test_dry_run_changes_nothing(self):
        svc = FakeService(live_agent())
        out = pv.publish_agent(C360, svc, dry_run=True)
        self.assertEqual(svc.methods(), ["GET"])
        self.assertEqual(out["publish"], "planned")
        self.assertEqual(
            out["pin_body"]["agent_endpoint"]["version_selector"]["version_selection_rules"][0]["agent_version"],
            "3")

    def test_publish_failure_raises(self):
        svc = FakeService(live_agent(), publish_status=400, publish_text="bad bot id")
        with self.assertRaises(RuntimeError):
            pv.publish_agent(SF, svc)

    def test_no_publish_stops_after_endpoint(self):
        svc = FakeService(live_agent())
        out = pv.publish_agent(SF, svc, publish=False)
        self.assertEqual(out["publish"], "skipped")
        self.assertNotIn("POST", svc.methods())
        self.assertIn("activity", svc.agent["agent_endpoint"]["protocol_configuration"])


if __name__ == "__main__":
    unittest.main()
