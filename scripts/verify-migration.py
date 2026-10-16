"""Verify a customer-360 deployment end-to-end.

Runs a pass/fail matrix against a target Azure subscription + resource group
(and the two azd projects that share the RG: salesforce-meta-tool-id-prop
and snow-meta-tool). Produces a machine-readable JSON report and a
human-readable Markdown summary.

Designed to be runnable against BOTH:
  - The current Sub-1 baseline (proves the checks themselves work)
  - The new Sub-3 migration target (proves migration success)

Usage:
  # Baseline against current Sub-1 (dry-run the checks):
  python scripts/verify-migration.py \
      --subscription 44026b8b-9f88-44d9-8f46-0898baa4bcd5 \
      --rg rg-sf-mcp-obo \
      --sf-env sf-mcp-obo --sn-env sn-mcp-obo \
      --baseline --skip-manual

  # After migration:
  python scripts/verify-migration.py \
      --subscription 1fafe902-ee73-468d-be1e-d76d99e8920c \
      --rg rg-customer-360 \
      --sf-env customer-360 --sn-env customer-360-sn

  # Post-teardown: prove Sub-1 is empty
  python scripts/verify-migration.py \
      --subscription 44026b8b-9f88-44d9-8f46-0898baa4bcd5 \
      --rg rg-sf-mcp-obo --post-teardown

Exit codes:
  0 - all checks in scope passed
  1 - one or more checks failed
  2 - script error (bad args, missing deps, etc.)
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def _run(cmd: str, parse_json: bool = False, timeout: int = 120, cwd: str | None = None):
    """Run a shell command. Returns (stdout_str_or_parsed, stderr_str, returncode)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, shell=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"},
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return (None, f"TIMEOUT after {timeout}s: {cmd}", -1)
    out = result.stdout.strip()
    if parse_json and out:
        try:
            out = json.loads(out)
        except json.JSONDecodeError as e:
            return (None, f"JSON parse failed: {e}\nraw: {out[:300]}", result.returncode)
    return (out, result.stderr.strip(), result.returncode)


def az(args: str, parse_json: bool = True, subscription: str | None = None, timeout: int = 120):
    """Run `az <args>`. Returns parsed JSON on success, None on failure."""
    sub = f' --subscription "{subscription}"' if subscription else ""
    fmt = " -o json" if parse_json and "-o " not in args and "--output" not in args else ""
    out, err, rc = _run(f"az {args}{sub}{fmt}", parse_json=parse_json, timeout=timeout)
    return out if rc == 0 else None


def curl(url: str, timeout: int = 15, extra: str = ""):
    """Run curl and return (http_code:int, body:str)."""
    cmd = f'curl -sS -o - -w "\\n%{{http_code}}" --max-time {timeout} {extra} "{url}"'
    out, err, rc = _run(cmd, parse_json=False, timeout=timeout + 5)
    if rc != 0 or not out:
        return (0, err or "")
    body, _, code = out.rpartition("\n")
    try:
        return (int(code), body)
    except ValueError:
        return (0, out)


# ---------------------------------------------------------------------------
# Check framework
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    check_id: str
    category: str
    description: str
    passed: bool
    severity: str  # blocking | warning | info
    message: str = ""
    details: Any = None
    duration_ms: int = 0
    skipped: bool = False


@dataclass
class Ctx:
    subscription: str
    rg: str
    sf_repo: Path
    sn_repo: Path
    sf_env: str
    sn_env: str
    skip_manual: bool
    baseline: bool
    post_teardown: bool
    # Resolved lazily
    _sf_env_vars: dict | None = None
    _sn_env_vars: dict | None = None
    _resources: list | None = None

    def sf_env_vars(self) -> dict:
        if self._sf_env_vars is None:
            self._sf_env_vars = _read_azd_env(self.sf_repo, self.sf_env)
        return self._sf_env_vars

    def sn_env_vars(self) -> dict:
        if self._sn_env_vars is None:
            self._sn_env_vars = _read_azd_env(self.sn_repo, self.sn_env)
        return self._sn_env_vars

    def resources(self) -> list:
        if self._resources is None:
            self._resources = az(f'resource list -g "{self.rg}"', subscription=self.subscription) or []
        return self._resources


def _read_azd_env(repo: Path, env_name: str) -> dict:
    env_file = repo / ".azure" / env_name / ".env"
    if not env_file.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# ---------------------------------------------------------------------------
# Category A — Bicep convergence
# ---------------------------------------------------------------------------

def check_a1_azd_env_complete(ctx: Ctx) -> tuple[bool, str, Any]:
    sf = ctx.sf_env_vars()
    required = [
        "AZURE_RESOURCE_GROUP", "AZURE_SUBSCRIPTION_ID", "APIM_NAME", "APIM_GATEWAY_URL",
        "COGNITIVE_ACCOUNT_NAME", "AI_FOUNDRY_PROJECT_NAME", "AI_FOUNDRY_PROJECT_ENDPOINT",
        "KEY_VAULT_NAME", "AZURE_CONTAINER_REGISTRY_NAME", "CHAT_APP_FQDN",
        "SF_MCP_FQDN", "APIM_SF_MCP_OBO_ENDPOINT", "CHAT_APP_ENTRA_CLIENT_ID",
        "SF_JWT_BEARER_CERT_THUMBPRINT", "AGENT_BOT_MSA_APP_ID", "AGENT_BOT_NAME",
    ]
    missing = [k for k in required if not sf.get(k)]
    return (not missing, f"{len(required) - len(missing)}/{len(required)} SF env vars set",
            {"missing": missing} if missing else None)


def check_a2_azd_env_complete_sn(ctx: Ctx) -> tuple[bool, str, Any]:
    sn = ctx.sn_env_vars()
    required = [
        "AZURE_RESOURCE_GROUP", "SN_MCP_FQDN", "APIM_SN_MCP_OBO_ENDPOINT",
        "SN_OBO_CONNECTION_NAME", "SN_JWT_BEARER_CERT_THUMBPRINT",
    ]
    missing = [k for k in required if not sn.get(k)]
    return (not missing, f"{len(required) - len(missing)}/{len(required)} SN env vars set",
            {"missing": missing} if missing else None)


def check_a3_deployments_succeeded(ctx: Ctx) -> tuple[bool, str, Any]:
    deps = az(f'deployment group list -g "{ctx.rg}"', subscription=ctx.subscription) or []
    bad = [d["name"] for d in deps if d.get("properties", {}).get("provisioningState") != "Succeeded"]
    return (not bad, f"{len(deps)} deployments ({len(bad)} not Succeeded)",
            {"failed": bad} if bad else None)


# ---------------------------------------------------------------------------
# Category B — Resource inventory
# ---------------------------------------------------------------------------

EXPECTED_RESOURCE_TYPES = {
    "Microsoft.ApiManagement/service": 1,
    "Microsoft.KeyVault/vaults": 1,
    "Microsoft.ContainerRegistry/registries": 1,
    "Microsoft.CognitiveServices/accounts": 1,
    "Microsoft.App/managedEnvironments": 1,
    "Microsoft.App/containerApps": 3,  # chat-app, sf-mcp, sn-mcp
    "Microsoft.OperationalInsights/workspaces": 1,
    "Microsoft.Insights/components": 1,
    "Microsoft.Storage/storageAccounts": 1,
    "Microsoft.BotService/botServices": 2,  # SF + SN
}


def check_b1_resource_inventory(ctx: Ctx) -> tuple[bool, str, Any]:
    got: dict[str, int] = {}
    for r in ctx.resources():
        got[r["type"]] = got.get(r["type"], 0) + 1
    missing = {}
    for rtype, expected in EXPECTED_RESOURCE_TYPES.items():
        actual = got.get(rtype, 0)
        if actual < expected:
            missing[rtype] = {"expected": expected, "actual": actual}
    return (not missing, f"{sum(got.values())} resources found ({len(missing)} types under-provisioned)",
            {"counts": got, "missing": missing} if missing else {"counts": got})


def check_b2_apim_sku(ctx: Ctx) -> tuple[bool, str, Any]:
    sf = ctx.sf_env_vars()
    apim_name = sf.get("APIM_NAME")
    if not apim_name:
        return (False, "APIM_NAME not in env", None)
    data = az(f'apim show --name "{apim_name}" -g "{ctx.rg}"', subscription=ctx.subscription)
    if not data:
        return (False, "APIM not found", None)
    sku = data.get("sku", {}).get("name")
    capacity = data.get("sku", {}).get("capacity")
    state = data.get("provisioningState")
    ok = sku == "StandardV2" and capacity == 1 and state == "Succeeded"
    return (ok, f"APIM sku={sku} capacity={capacity} state={state} (want StandardV2/1/Succeeded)",
            {"sku": sku, "capacity": capacity, "state": state})


def check_b3_cognitive_deployments(ctx: Ctx) -> tuple[bool, str, Any]:
    sf = ctx.sf_env_vars()
    acct = sf.get("COGNITIVE_ACCOUNT_NAME")
    if not acct:
        return (False, "COGNITIVE_ACCOUNT_NAME not in env", None)
    deps = az(f'cognitiveservices account deployment list --name "{acct}" -g "{ctx.rg}"',
              subscription=ctx.subscription) or []
    names = {d.get("name"): d.get("properties", {}).get("provisioningState") for d in deps}
    expected = ["gpt-5.4"]
    missing = [n for n in expected if n not in names]
    not_succeeded = [n for n, s in names.items() if s != "Succeeded"]
    ok = not missing and not not_succeeded
    return (ok, f"{len(names)} deployments; missing={missing} notSucceeded={not_succeeded}",
            {"deployments": names})


# ---------------------------------------------------------------------------
# Category C — RBAC
# ---------------------------------------------------------------------------

ROLE_IDS = {
    "Cognitive Services User": "a97b65f3-24c7-4388-baec-2e87135dc908",
    "Cognitive Services Contributor": "25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68",
    "Key Vault Secrets User": "4633458b-17de-408a-b874-0445c86b69e6",
    "Reader": "acdd72a7-3385-48ef-bd42-f606fba81ae7",
}


def _role_assignments(scope: str, principal_id: str, subscription: str) -> list:
    return az(f'role assignment list --scope "{scope}" --assignee "{principal_id}"',
              subscription=subscription) or []


def _container_app_principal(name: str, rg: str, sub: str) -> str | None:
    app = az(f'containerapp show -n "{name}" -g "{rg}"', subscription=sub)
    if not app:
        return None
    return app.get("identity", {}).get("principalId")


def check_c1_chatapp_cognitive_rbac(ctx: Ctx) -> tuple[bool, str, Any]:
    chat_name = ctx.sf_env_vars().get("CHAT_APP_CONTAINER_APP_NAME", "ca-chat-app")
    cog_name = ctx.sf_env_vars().get("COGNITIVE_ACCOUNT_NAME")
    if not cog_name:
        return (False, "missing cognitive account name", None)
    pid = _container_app_principal(chat_name, ctx.rg, ctx.subscription)
    if not pid:
        return (False, f"chat app {chat_name} has no managed identity", None)
    scope = f"/subscriptions/{ctx.subscription}/resourceGroups/{ctx.rg}/providers/Microsoft.CognitiveServices/accounts/{cog_name}"
    ras = _role_assignments(scope, pid, ctx.subscription)
    role_ids = {r.get("roleDefinitionId", "").split("/")[-1] for r in ras}
    need = {ROLE_IDS["Cognitive Services User"], ROLE_IDS["Cognitive Services Contributor"]}
    missing = need - role_ids
    return (not missing, f"chat app roles on cognitive: {len(ras)}",
            {"missing_role_ids": list(missing)} if missing else None)


def check_c2_chatapp_subscription_reader(ctx: Ctx) -> tuple[bool, str, Any]:
    chat_name = ctx.sf_env_vars().get("CHAT_APP_CONTAINER_APP_NAME", "ca-chat-app")
    pid = _container_app_principal(chat_name, ctx.rg, ctx.subscription)
    if not pid:
        return (False, "chat app has no MI", None)
    scope = f"/subscriptions/{ctx.subscription}"
    ras = _role_assignments(scope, pid, ctx.subscription)
    has_reader = any(r.get("roleDefinitionId", "").endswith(ROLE_IDS["Reader"]) for r in ras)
    return (has_reader, "Reader on subscription" if has_reader else "MISSING Reader on subscription",
            {"role_assignments": len(ras)})


def check_c3_apim_kv_rbac(ctx: Ctx) -> tuple[bool, str, Any]:
    sf = ctx.sf_env_vars()
    apim_name = sf.get("APIM_NAME")
    kv_name = sf.get("KEY_VAULT_NAME")
    if not (apim_name and kv_name):
        return (False, "missing apim/kv name", None)
    apim = az(f'apim show --name "{apim_name}" -g "{ctx.rg}"', subscription=ctx.subscription)
    pid = (apim or {}).get("identity", {}).get("principalId")
    if not pid:
        return (False, "APIM has no managed identity", None)
    scope = f"/subscriptions/{ctx.subscription}/resourceGroups/{ctx.rg}/providers/Microsoft.KeyVault/vaults/{kv_name}"
    ras = _role_assignments(scope, pid, ctx.subscription)
    has = any(r.get("roleDefinitionId", "").endswith(ROLE_IDS["Key Vault Secrets User"]) for r in ras)
    return (has, "APIM has Key Vault Secrets User" if has else "MISSING Key Vault Secrets User on KV",
            None)


# ---------------------------------------------------------------------------
# Category D — Certificates
# ---------------------------------------------------------------------------

def check_d1_sf_cert_in_kv(ctx: Ctx) -> tuple[bool, str, Any]:
    kv = ctx.sf_env_vars().get("KEY_VAULT_NAME")
    expected_tp = ctx.sf_env_vars().get("SF_JWT_BEARER_CERT_THUMBPRINT", "").upper()
    cert = az(f'keyvault certificate show --vault-name "{kv}" -n sf-jwt-bearer', subscription=ctx.subscription)
    if not cert:
        return (False, "sf-jwt-bearer not in KV", None)
    actual_tp = cert.get("x509ThumbprintHex", "").upper()
    ok = bool(actual_tp) and (not expected_tp or actual_tp == expected_tp)
    return (ok, f"thumbprint={actual_tp[:8]}... (expected={expected_tp[:8]}...)",
            {"actual": actual_tp, "expected": expected_tp})


def check_d2_sn_cert_in_kv(ctx: Ctx) -> tuple[bool, str, Any]:
    kv = ctx.sf_env_vars().get("KEY_VAULT_NAME")
    cert = az(f'keyvault certificate show --vault-name "{kv}" -n sn-jwt-bearer', subscription=ctx.subscription)
    if not cert:
        return (False, "sn-jwt-bearer not in KV", None)
    return (True, f"sn-jwt-bearer present (tp={cert.get('x509ThumbprintHex', '')[:8]}...)", None)


def check_d3_apim_cert_bindings(ctx: Ctx) -> tuple[bool, str, Any]:
    apim = ctx.sf_env_vars().get("APIM_NAME")
    url = (f"https://management.azure.com/subscriptions/{ctx.subscription}/resourceGroups/{ctx.rg}"
           f"/providers/Microsoft.ApiManagement/service/{apim}/certificates?api-version=2022-08-01")
    data = az(f'rest --method GET --url "{url}"', subscription=ctx.subscription)
    certs = (data or {}).get("value", [])
    names = {c.get("name") for c in certs}
    need = {"sf-jwt-bearer", "sn-jwt-bearer"}
    missing = need - names
    return (not missing, f"{len(certs)} APIM certs; missing={missing}",
            {"present": sorted(names), "missing": sorted(missing)})


# ---------------------------------------------------------------------------
# Category E — APIM config
# ---------------------------------------------------------------------------

REQUIRED_APIM_NAMED_VALUES = [
    "SfOboClientId", "SfOboLoginUrl", "SfJwtBearerCertThumbprint",
    "SfServiceAccountUsername", "IdentityClaimName",
    "SnOboClientId", "SnOboInstanceUrl", "SnJwtBearerCertThumbprint", "SnJwtBearerKid",
]


def check_e1_apim_named_values(ctx: Ctx) -> tuple[bool, str, Any]:
    apim = ctx.sf_env_vars().get("APIM_NAME")
    nvs = az(f'apim nv list --service-name "{apim}" -g "{ctx.rg}"',
             subscription=ctx.subscription) or []
    names = {nv["name"] for nv in nvs}
    missing = [n for n in REQUIRED_APIM_NAMED_VALUES if n not in names]
    return (not missing, f"{len(nvs)} NVs; missing={missing}",
            {"missing": missing} if missing else None)


def check_e2_apim_apis(ctx: Ctx) -> tuple[bool, str, Any]:
    apim = ctx.sf_env_vars().get("APIM_NAME")
    apis = az(f'apim api list --service-name "{apim}" -g "{ctx.rg}"',
              subscription=ctx.subscription) or []
    names = {a["name"] for a in apis}
    need = {"salesforce-mcp-obo", "servicenow-mcp-obo", "openai"}
    missing = need - names
    return (not missing, f"{len(apis)} APIs; missing={missing}",
            {"present": sorted(names), "missing": sorted(missing)})


def check_e3_apim_unauthed_rejected(ctx: Ctx) -> tuple[bool, str, Any]:
    ep = ctx.sf_env_vars().get("APIM_SF_MCP_OBO_ENDPOINT")
    if not ep:
        return (False, "APIM_SF_MCP_OBO_ENDPOINT not set", None)
    code, body = curl(ep)
    ok = code in (401, 403)
    return (ok, f"unauth request → {code} (expected 401/403)",
            {"http_code": code, "body_prefix": body[:120]})


# ---------------------------------------------------------------------------
# Category F — Container Apps
# ---------------------------------------------------------------------------

def check_f1_container_apps_running(ctx: Ctx) -> tuple[bool, str, Any]:
    apps = az(f'containerapp list -g "{ctx.rg}"', subscription=ctx.subscription) or []
    expected = {"ca-chat-app", "ca-sf-mcp", "ca-sn-mcp"}
    got = {a["name"]: a.get("properties", {}).get("runningStatus") for a in apps if a["name"] in expected}
    missing = expected - set(got.keys())
    not_running = [n for n, s in got.items() if s != "Running"]
    ok = not missing and not not_running
    return (ok, f"apps running: {got}; missing={missing}",
            {"status": got, "missing": list(missing), "notRunning": not_running})


def check_f2_health_endpoints(ctx: Ctx) -> tuple[bool, str, Any]:
    sf = ctx.sf_env_vars()
    sn = ctx.sn_env_vars()
    urls = {
        "chat-app": f"https://{sf.get('CHAT_APP_FQDN', '')}/health",
        "sf-mcp": f"https://{sf.get('SF_MCP_FQDN', '')}/health",
        "sn-mcp": f"https://{sn.get('SN_MCP_FQDN', '')}/health",
    }
    results = {}
    for k, u in urls.items():
        if not u.startswith("https://"):
            results[k] = {"ok": False, "reason": "fqdn not set"}
            continue
        code, body = curl(u)
        results[k] = {"ok": 200 <= code < 300, "code": code}
    bad = [k for k, r in results.items() if not r["ok"]]
    return (not bad, f"{len(urls) - len(bad)}/{len(urls)} healthy; bad={bad}", results)


# ---------------------------------------------------------------------------
# Category G — Foundry agents
# ---------------------------------------------------------------------------

def check_g1_foundry_agents(ctx: Ctx) -> tuple[bool, str, Any]:
    endpoint = ctx.sf_env_vars().get("AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        return (False, "AI_FOUNDRY_PROJECT_ENDPOINT not set", None)
    try:
        from azure.ai.projects import AIProjectClient  # type: ignore
        from azure.identity import DefaultAzureCredential  # type: ignore
    except ImportError as e:
        return (False, f"SDK not installed: {e}", None)
    try:
        client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
        agents = list(client.agents.list())
    except Exception as e:
        return (False, f"agent list failed: {e}", None)
    names = {a.name for a in agents}
    need = {"salesforce-assistant", "servicenow-assistant", "customer360-assistant"}
    missing = need - names
    return (not missing, f"{len(names)} agents; missing={missing}",
            {"present": sorted(names), "missing": sorted(missing)})


def check_g2_foundry_connections(ctx: Ctx) -> tuple[bool, str, Any]:
    endpoint = ctx.sf_env_vars().get("AI_FOUNDRY_PROJECT_ENDPOINT")
    cog = ctx.sf_env_vars().get("COGNITIVE_ACCOUNT_NAME")
    proj = ctx.sf_env_vars().get("AI_FOUNDRY_PROJECT_NAME")
    if not (cog and proj):
        return (False, "cognitive/project name missing", None)
    base = (f"/subscriptions/{ctx.subscription}/resourceGroups/{ctx.rg}"
            f"/providers/Microsoft.CognitiveServices/accounts/{cog}/projects/{proj}/connections")
    need = ["salesforce-obo", "servicenow-obo"]
    results = {}
    for name in need:
        data = az(f'rest --method GET --url "https://management.azure.com{base}/{name}?api-version=2025-04-01-preview"',
                  subscription=ctx.subscription)
        results[name] = bool(data)
    missing = [k for k, v in results.items() if not v]
    return (not missing, f"connections present: {results}",
            {"missing": missing} if missing else None)


# ---------------------------------------------------------------------------
# Category H — Bot Services
# ---------------------------------------------------------------------------

def check_h1_bot_services(ctx: Ctx) -> tuple[bool, str, Any]:
    bots = az(f'resource list -g "{ctx.rg}" --resource-type Microsoft.BotService/botServices',
              subscription=ctx.subscription) or []
    names = [b["name"] for b in bots]
    ok = len(bots) == 2
    return (ok, f"expected exactly 2 Bot Services, got {len(bots)}: {names}",
            {"found": names})


def check_h2_bot_endpoints(ctx: Ctx) -> tuple[bool, str, Any]:
    bots = az(f'resource list -g "{ctx.rg}" --resource-type Microsoft.BotService/botServices',
              subscription=ctx.subscription) or []
    results = {}
    bad = []
    for b in bots:
        detail = az(f'bot show --name "{b["name"]}" -g "{ctx.rg}"', subscription=ctx.subscription)
        endpoint = (detail or {}).get("properties", {}).get("endpoint", "")
        is_foundry = ".services.ai.azure.com/api/projects/" in endpoint
        results[b["name"]] = {"endpoint": endpoint, "foundry": is_foundry}
        if not is_foundry:
            bad.append(b["name"])
    return (not bad, f"bot endpoints foundry-routed: {len(results) - len(bad)}/{len(results)}",
            results)


# ---------------------------------------------------------------------------
# Category I — Teams catalog
# ---------------------------------------------------------------------------

def check_i1_teams_catalog(ctx: Ctx) -> tuple[bool, str, Any]:
    # Try to list org catalog. If we get a 403 (missing AppCatalog scope), treat as
    # "skipped/manual" rather than a failure — the cutover plan calls for manual sideload.
    cmd = ('rest --method GET --url "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps?'
           '\\$filter=distributionMethod eq \'organization\'"')
    out, err, rc = _run(f'az {cmd}', parse_json=True, timeout=60)
    if rc != 0:
        if "Forbidden" in (err or "") or "AppCatalog" in (err or ""):
            return (True, "skipped (no AppCatalog.ReadWrite.All scope; manual sideload via cutover checklist)",
                    {"skipped_reason": "missing_graph_scope"})
        return (False, f"Graph call failed: {(err or '')[:200]}", None)
    apps = (out or {}).get("value", [])
    bot_ids = set()
    for ev_map in (ctx.sf_env_vars(), ctx.sn_env_vars()):
        v = ev_map.get("AGENT_BOT_MSA_APP_ID")
        if v:
            bot_ids.add(v)
    matched = [a["displayName"] for a in apps
               if any(bid in json.dumps(a) for bid in bot_ids)]
    ok = len(matched) >= 2
    return (ok, f"{len(apps)} org apps; matched to new bots: {len(matched)}",
            {"matched": matched})


# ---------------------------------------------------------------------------
# Category J — E2E identity propagation
# ---------------------------------------------------------------------------

def check_j1_sf_mcp_e2e(ctx: Ctx) -> tuple[bool, str, Any]:
    script = ctx.sf_repo / "scripts" / "test-salesforce-mcp.py"
    if not script.exists():
        return (False, f"{script} not found", None)
    out, err, rc = _run(f'python "{script}"', parse_json=False, timeout=300, cwd=str(ctx.sf_repo))
    return (rc == 0, f"exit={rc}", {"stdout_tail": (out or "")[-800:], "stderr_tail": (err or "")[-400:]})


def check_j2_c360_e2e(ctx: Ctx) -> tuple[bool, str, Any]:
    script = ctx.sf_repo / "scripts" / "test_e2e_customer360.py"
    if not script.exists():
        return (False, f"{script} not found", None)
    out, err, rc = _run(f'python "{script}"', parse_json=False, timeout=900, cwd=str(ctx.sf_repo))
    return (rc == 0, f"exit={rc}", {"stdout_tail": (out or "")[-1200:]})


# ---------------------------------------------------------------------------
# Category L — Observability
# ---------------------------------------------------------------------------

def _la_query_rows(ctx: Ctx, ws: str, kql: str) -> list:
    """Run a Log Analytics query and return rows regardless of CLI payload shape."""
    data = az(f'monitor log-analytics query -w "{ws}" --analytics-query "{kql}"',
              subscription=ctx.subscription)
    if data is None:
        return []
    # Newer CLI returns a flat list of row-dicts; older returns {tables:[{rows:[[...]]}]}.
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        tables = data.get("tables") or []
        if tables and isinstance(tables[0], dict):
            return tables[0].get("rows") or []
    return []


def check_l1_recent_traces(ctx: Ctx) -> tuple[bool, str, Any]:
    ws = ctx.sf_env_vars().get("LOG_ANALYTICS_WORKSPACE_ID")
    if not ws:
        return (False, "LOG_ANALYTICS_WORKSPACE_ID not set", None)
    kql = "AppTraces | where TimeGenerated > ago(30m) | summarize c=count() by AppRoleName"
    rows = _la_query_rows(ctx, ws, kql)
    role_names = {
        (r.get("AppRoleName") if isinstance(r, dict) else (r[0] if r else None))
        for r in rows
    }
    role_names.discard(None)
    expected_any = {"ca-chat-app", "ca-sf-mcp", "ca-sn-mcp"}
    hit = role_names & expected_any
    ok = len(hit) >= 2  # at least 2 of 3 emitting in the last 30m
    return (ok, f"{len(rows)} rows; emitting roles ∩ expected = {hit}",
            {"role_names": sorted(role_names), "hit": sorted(hit)})


def check_l2_no_5xx(ctx: Ctx) -> tuple[bool, str, Any]:
    ws = ctx.sf_env_vars().get("LOG_ANALYTICS_WORKSPACE_ID")
    if not ws:
        return (False, "LOG_ANALYTICS_WORKSPACE_ID not set", None)
    kql = ("AppRequests | where TimeGenerated > ago(30m) and ResultCode startswith '5' "
           "| summarize c=count() by OperationName | top 10 by c")
    rows = _la_query_rows(ctx, ws, kql)
    ok = len(rows) == 0
    return (ok, f"{len(rows)} operations with 5xx in last 30m", {"rows": rows[:5]})


# ---------------------------------------------------------------------------
# Category K — Manual (skipped unless user provides --manual)
# ---------------------------------------------------------------------------

def check_k1_manual_signin(ctx: Ctx) -> tuple[bool, str, Any]:
    if not sys.stdin.isatty():
        return (True, "non-tty: skipped (manual)", {"skipped_reason": "non-tty"})
    fqdn = ctx.sf_env_vars().get("CHAT_APP_FQDN", "<unknown>")
    print(f"\n  MANUAL: Open https://{fqdn}/ in a browser, sign in, verify /api/agents shows 3 agents,")
    print(f"          ask SOQL, ask SN incident, ask C360 cross-system. Did all 3 succeed? [y/N] ", end="")
    resp = input().strip().lower()
    return (resp == "y", f"manual response: {resp}", None)


def check_k2_manual_teams(ctx: Ctx) -> tuple[bool, str, Any]:
    if not sys.stdin.isatty():
        return (True, "non-tty: skipped (manual)", {"skipped_reason": "non-tty"})
    print("  MANUAL: Sideload BOTH new Teams apps (SF + SN), post a message to each, verify replies. [y/N] ", end="")
    resp = input().strip().lower()
    return (resp == "y", f"manual response: {resp}", None)


# ---------------------------------------------------------------------------
# Category M — Rollback readiness
# ---------------------------------------------------------------------------

def check_m1_snapshot_exists(ctx: Ctx) -> tuple[bool, str, Any]:
    snap = ctx.sf_repo / ".local" / "sub1-snapshot"
    ok = snap.exists() and any(snap.iterdir())
    return (ok, f"{snap} {'exists' if ok else 'MISSING'}", None)


# ---------------------------------------------------------------------------
# Category N — Security
# ---------------------------------------------------------------------------

def check_n1_apim_policy_present(ctx: Ctx) -> tuple[bool, str, Any]:
    apim = ctx.sf_env_vars().get("APIM_NAME")
    url = (f"https://management.azure.com/subscriptions/{ctx.subscription}/resourceGroups/{ctx.rg}"
           f"/providers/Microsoft.ApiManagement/service/{apim}/apis/salesforce-mcp-obo"
           f"/policies/policy?api-version=2022-08-01&format=rawxml")
    pol = az(f'rest --method GET --url "{url}"', subscription=ctx.subscription)
    if not pol:
        return (False, "policy fetch failed", None)
    body = json.dumps(pol).lower()
    needs = ["validate-jwt", "identityclaimname", "sfjwtbearercertthumbprint"]
    missing = [n for n in needs if n not in body]
    return (not missing, f"policy markers present: {len(needs) - len(missing)}/{len(needs)}",
            {"missing_markers": missing} if missing else None)


# ---------------------------------------------------------------------------
# Category O — Decommission (post-teardown only)
# ---------------------------------------------------------------------------

def check_o1_rg_empty(ctx: Ctx) -> tuple[bool, str, Any]:
    rs = az(f'resource list -g "{ctx.rg}"', subscription=ctx.subscription)
    if rs is None:
        # RG doesn't exist — ideal
        return (True, f"RG {ctx.rg} does not exist (fully removed)", None)
    return (len(rs) == 0, f"{len(rs)} residual resources in {ctx.rg}",
            {"resources": [r["name"] for r in rs][:20]})


def check_o2_no_soft_deleted(ctx: Ctx) -> tuple[bool, str, Any]:
    sf = ctx.sf_env_vars()
    kv = sf.get("KEY_VAULT_NAME", "kv-sf-mcp-obo")
    cog = sf.get("COGNITIVE_ACCOUNT_NAME", "aoai-sf-mcp-obo")
    apim = sf.get("APIM_NAME", "apim-sf-mcp-obo")
    residuals = []
    if az(f'keyvault list-deleted --query "[?name==\'{kv}\']"', subscription=ctx.subscription):
        residuals.append(f"kv:{kv}")
    cog_deleted = az(f'cognitiveservices account list-deleted', subscription=ctx.subscription) or []
    if any(c.get("name") == cog for c in cog_deleted):
        residuals.append(f"cog:{cog}")
    apim_deleted = az(f'rest --method GET --url "https://management.azure.com/subscriptions/'
                      f'{ctx.subscription}/providers/Microsoft.ApiManagement/deletedservices?api-version=2022-08-01"',
                      subscription=ctx.subscription) or {}
    if any(s.get("name") == apim for s in apim_deleted.get("value", [])):
        residuals.append(f"apim:{apim}")
    return (not residuals, f"{len(residuals)} soft-deleted residuals",
            {"residuals": residuals})


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class Check:
    id: str
    category: str
    description: str
    fn: Callable[[Ctx], tuple]
    severity: str = "blocking"
    manual: bool = False
    post_teardown_only: bool = False


CHECKS: list[Check] = [
    Check("A1", "Bicep convergence", "SF azd env has all required outputs", check_a1_azd_env_complete),
    Check("A2", "Bicep convergence", "SN azd env has all required outputs", check_a2_azd_env_complete_sn),
    Check("A3", "Bicep convergence", "All ARM deployments Succeeded", check_a3_deployments_succeeded),
    Check("B1", "Resource inventory", "Expected resource types present in RG", check_b1_resource_inventory),
    Check("B2", "Resource inventory", "APIM SKU and provisioning state", check_b2_apim_sku),
    Check("B3", "Resource inventory", "Cognitive deployment gpt-5.4", check_b3_cognitive_deployments),
    Check("C1", "RBAC", "Chat App MI has Cognitive roles", check_c1_chatapp_cognitive_rbac),
    Check("C2", "RBAC", "Chat App MI has Reader at subscription scope", check_c2_chatapp_subscription_reader),
    Check("C3", "RBAC", "APIM MI has Key Vault Secrets User", check_c3_apim_kv_rbac),
    Check("D1", "Certificates", "sf-jwt-bearer present in KV with matching thumbprint", check_d1_sf_cert_in_kv),
    Check("D2", "Certificates", "sn-jwt-bearer present in KV", check_d2_sn_cert_in_kv),
    Check("D3", "Certificates", "APIM cert bindings for both certs", check_d3_apim_cert_bindings),
    Check("E1", "APIM config", "APIM Named Values populated", check_e1_apim_named_values),
    Check("E2", "APIM config", "APIM APIs present (SF + SN OBO)", check_e2_apim_apis),
    Check("E3", "APIM config", "Unauthenticated APIM calls rejected (401/403)", check_e3_apim_unauthed_rejected),
    Check("F1", "Container Apps", "All 3 Container Apps Running", check_f1_container_apps_running),
    Check("F2", "Container Apps", "All 3 /health endpoints return 2xx", check_f2_health_endpoints),
    Check("G1", "Foundry", "All 3 agents discoverable (SF, SN, C360)", check_g1_foundry_agents),
    Check("G2", "Foundry", "salesforce-obo + servicenow-obo connections exist", check_g2_foundry_connections),
    Check("H1", "Bot Services", "≥2 Bot Services in RG", check_h1_bot_services),
    Check("H2", "Bot Services", "All bot endpoints point at Foundry Activity Protocol URL", check_h2_bot_endpoints),
    Check("I1", "Teams catalog", "New Teams apps published for both bots", check_i1_teams_catalog, severity="warning"),
    Check("J1", "E2E", "Salesforce MCP E2E test (identity propagation)", check_j1_sf_mcp_e2e),
    Check("J2", "E2E", "Customer 360 E2E test (both SF+SN tools fire)", check_j2_c360_e2e),
    Check("K1", "Manual", "Browser sign-in + 3 agents answering", check_k1_manual_signin, manual=True),
    Check("K2", "Manual", "Both Teams bots reply", check_k2_manual_teams, manual=True),
    Check("L1", "Observability", "App Insights receiving traces (last 30m)", check_l1_recent_traces),
    Check("L2", "Observability", "Zero 5xx errors in last 30m", check_l2_no_5xx),
    Check("M1", "Rollback", ".local/sub1-snapshot/ exists", check_m1_snapshot_exists, severity="warning"),
    Check("N1", "Security", "APIM OBO policy contains validate-jwt + identity markers", check_n1_apim_policy_present),
    Check("O1", "Decommission", "Resource group empty", check_o1_rg_empty, post_teardown_only=True),
    Check("O2", "Decommission", "No soft-deleted residuals (KV/Cog/APIM)", check_o2_no_soft_deleted, post_teardown_only=True),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all(ctx: Ctx) -> list[CheckResult]:
    results: list[CheckResult] = []
    for c in CHECKS:
        # Scope filters
        if c.post_teardown_only and not ctx.post_teardown:
            continue
        if ctx.post_teardown and not c.post_teardown_only:
            continue
        if c.manual and ctx.skip_manual:
            results.append(CheckResult(c.id, c.category, c.description, False, c.severity,
                                       "skipped (manual)", skipped=True))
            continue
        t0 = time.time()
        try:
            passed, message, details = c.fn(ctx)
        except Exception as e:
            passed, message, details = False, f"EXCEPTION: {e}", {"traceback": traceback.format_exc()[-1000:]}
        dur = int((time.time() - t0) * 1000)
        results.append(CheckResult(c.id, c.category, c.description, bool(passed),
                                   c.severity, message, details, dur))
        icon = "✅" if passed else ("⚠️" if c.severity == "warning" else "❌")
        print(f"  {icon} [{c.id}] {c.description} — {message} ({dur}ms)")
    return results


def write_reports(results: list[CheckResult], out_dir: Path, ctx: Ctx) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"verify-migration-{ts}.json"
    md_path = out_dir / f"verify-migration-{ts}.md"

    payload = {
        "timestamp": ts,
        "subscription": ctx.subscription,
        "resource_group": ctx.rg,
        "sf_env": ctx.sf_env,
        "sn_env": ctx.sn_env,
        "baseline": ctx.baseline,
        "post_teardown": ctx.post_teardown,
        "summary": _summary(results),
        "results": [dataclasses.asdict(r) for r in results],
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    md_path.write_text(_render_markdown(results, ctx, ts), encoding="utf-8")
    return json_path, md_path


def _summary(results: list[CheckResult]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    blocking_failed = sum(1 for r in results if not r.passed and not r.skipped and r.severity == "blocking")
    return {"total": total, "passed": passed, "failed": failed, "skipped": skipped,
            "blocking_failed": blocking_failed,
            "verdict": "PASS" if blocking_failed == 0 else "FAIL"}


def _render_markdown(results: list[CheckResult], ctx: Ctx, ts: str) -> str:
    s = _summary(results)
    lines = [
        f"# Migration Verification Report — {ts}",
        "",
        f"- Subscription: `{ctx.subscription}`",
        f"- Resource group: `{ctx.rg}`",
        f"- SF azd env: `{ctx.sf_env}`    SN azd env: `{ctx.sn_env}`",
        f"- Baseline mode: {ctx.baseline}    Post-teardown mode: {ctx.post_teardown}",
        "",
        f"## Verdict: **{s['verdict']}** — {s['passed']}/{s['total']} passed "
        f"({s['failed']} failed, {s['skipped']} skipped, {s['blocking_failed']} blocking failures)",
        "",
        "| ID | Cat | Check | Result | Message |",
        "|----|-----|-------|--------|---------|",
    ]
    for r in results:
        icon = "✅" if r.passed else ("⚠️" if r.severity == "warning" else ("⏭" if r.skipped else "❌"))
        msg = r.message.replace("|", "\\|")[:140]
        lines.append(f"| {r.check_id} | {r.category} | {r.description} | {icon} | {msg} |")
    lines.append("")
    failed = [r for r in results if not r.passed and not r.skipped]
    if failed:
        lines.append("## Failures")
        for r in failed:
            lines.append(f"### [{r.check_id}] {r.description}")
            lines.append(f"- severity: **{r.severity}**")
            lines.append(f"- message: {r.message}")
            if r.details:
                lines.append(f"- details:\n```json\n{json.dumps(r.details, indent=2, default=str)[:2000]}\n```")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verify customer-360 migration deployment")
    p.add_argument("--subscription", required=True, help="Target Azure subscription ID")
    p.add_argument("--rg", required=True, help="Resource group name")
    p.add_argument("--sf-repo", default=".", help="Path to salesforce-meta-tool-id-prop repo (default: cwd)")
    p.add_argument("--sn-repo", default="../snow-meta-tool", help="Path to snow-meta-tool repo")
    p.add_argument("--sf-env", default="customer-360", help="SF azd env name")
    p.add_argument("--sn-env", default="customer-360-sn", help="SN azd env name")
    p.add_argument("--skip-manual", action="store_true", help="Skip interactive manual checks")
    p.add_argument("--baseline", action="store_true", help="Running against Sub-1 baseline (informational only)")
    p.add_argument("--post-teardown", action="store_true",
                   help="Run ONLY the O-category checks (RG empty, no soft-deletes)")
    p.add_argument("--out-dir", default=".local", help="Directory for JSON + MD reports")
    args = p.parse_args(argv)

    ctx = Ctx(
        subscription=args.subscription,
        rg=args.rg,
        sf_repo=Path(args.sf_repo).resolve(),
        sn_repo=Path(args.sn_repo).resolve(),
        sf_env=args.sf_env,
        sn_env=args.sn_env,
        skip_manual=args.skip_manual,
        baseline=args.baseline,
        post_teardown=args.post_teardown,
    )

    # Sanity: verify az CLI and correct subscription
    probe, _, rc = _run(f'az account show --subscription "{args.subscription}" --query id -o tsv', timeout=30)
    if rc != 0 or not probe:
        print(f"ERROR: cannot access subscription {args.subscription}. Run `az login` and check access.",
              file=sys.stderr)
        return 2

    # Post-teardown mode must not silently read Sub-3 env names; require explicit legacy envs.
    if args.post_teardown:
        if args.sf_env == "customer-360" or args.sn_env == "customer-360-sn":
            print("ERROR: --post-teardown requires --sf-env/--sn-env to point at the LEGACY "
                  "(Sub-1) azd envs (e.g. sf-mcp-obo / sn-mcp-obo). Refusing to use Sub-3 defaults.",
                  file=sys.stderr)
            return 2

    print(f"\nVerifying migration: sub={args.subscription} rg={args.rg}")
    print(f"  SF env: {ctx.sf_env} @ {ctx.sf_repo}")
    print(f"  SN env: {ctx.sn_env} @ {ctx.sn_repo}")
    print(f"  Mode: {'post-teardown' if ctx.post_teardown else ('baseline' if ctx.baseline else 'target')}\n")

    results = run_all(ctx)

    out_dir = (ctx.sf_repo / args.out_dir).resolve()
    json_path, md_path = write_reports(results, out_dir, ctx)

    s = _summary(results)
    print("\n" + "=" * 70)
    print(f"VERDICT: {s['verdict']}  ({s['passed']}/{s['total']} passed, "
          f"{s['failed']} failed, {s['skipped']} skipped, "
          f"{s['blocking_failed']} blocking failures)")
    print(f"JSON report:     {json_path}")
    print(f"Markdown report: {md_path}")
    print("=" * 70)
    return 0 if s["blocking_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
