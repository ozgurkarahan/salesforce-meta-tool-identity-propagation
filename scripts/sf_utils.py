"""Shared Salesforce and CLI primitives for setup scripts.

Extracted from the individual setup scripts to eliminate duplication.
Used by setup-sf-org.py (the consolidated orchestrator).
"""

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request

os.environ.setdefault("PYTHONIOENCODING", "utf-8")


# ---------------------------------------------------------------------------
# Shell execution
# ---------------------------------------------------------------------------

def run(cmd: str, parse_json: bool = False, cwd: str | None = None):
    """Run a shell command and return stdout (or parsed JSON).

    Returns None on non-zero exit code or empty output.
    """
    result = subprocess.run(
        cmd, capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        cwd=cwd,
    )
    if result.returncode != 0:
        if result.stderr:
            print(f"  stderr: {result.stderr[:500]}")
        return None
    out = result.stdout.strip()
    if not out:
        return None
    if parse_json:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None
    return out


def run_interactive(cmd: str) -> int:
    """Run a command with visible stdin/stdout (for browser login etc.)."""
    result = subprocess.run(
        cmd, shell=True,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )
    return result.returncode


# ---------------------------------------------------------------------------
# SF org helpers
# ---------------------------------------------------------------------------

def get_org_info(org: str) -> dict | None:
    """Get org display info dict (instanceUrl, accessToken, username, etc.)."""
    result = run(f"sf org display -o {org} --json", parse_json=True)
    if not result:
        return None
    return result.get("result", {})


def get_org_domain(org: str) -> str | None:
    """Get the org's My Domain (e.g., 'mycompany.my.salesforce.com')."""
    info = get_org_info(org)
    if not info:
        return None
    instance_url = info.get("instanceUrl", "")
    if instance_url:
        return instance_url.replace("https://", "").replace("http://", "")
    return None


def get_access_token(org: str) -> tuple[str | None, str | None]:
    """Get (access_token, instance_url) for REST API calls."""
    info = get_org_info(org)
    if not info:
        return None, None
    return info.get("accessToken"), info.get("instanceUrl")


# ---------------------------------------------------------------------------
# SOQL helpers
# ---------------------------------------------------------------------------

def soql_query(org: str, soql: str) -> list[dict]:
    """Run a SOQL query and return the records list."""
    result = run(f'sf data query -o {org} -q "{soql}" --json', parse_json=True)
    if not result:
        return []
    return result.get("result", {}).get("records", [])


def tooling_query(org: str, soql: str) -> list[dict]:
    """Run a Tooling API SOQL query and return the records list."""
    result = run(
        f'sf data query -o {org} -t -q "{soql}" --json', parse_json=True,
    )
    if not result:
        return []
    return result.get("result", {}).get("records", [])


def query_profile_id(org: str, profile_name: str) -> str | None:
    """Query a Profile ID by display name."""
    records = soql_query(org, f"SELECT Id FROM Profile WHERE Name = '{profile_name}'")
    return records[0]["Id"] if records else None


def query_user(org: str, username: str) -> dict | None:
    """Query a User by username. Returns the record dict or None."""
    records = soql_query(
        org,
        "SELECT Id, Username, Email, ProfileId, IsActive, FederationIdentifier "
        f"FROM User WHERE Username = '{username}'",
    )
    return records[0] if records else None


# ---------------------------------------------------------------------------
# Metadata deployment
# ---------------------------------------------------------------------------

def init_sfdx_project(work_dir: str):
    """Create a minimal sfdx-project.json + force-app dir for sf CLI."""
    project_json = os.path.join(work_dir, "sfdx-project.json")
    if not os.path.exists(project_json):
        with open(project_json, "w") as f:
            json.dump({
                "packageDirectories": [{"path": "force-app", "default": True}],
                "namespace": "",
                "sfdcLoginUrl": "https://login.salesforce.com",
                "sourceApiVersion": "62.0",
            }, f, indent=2)
    os.makedirs(os.path.join(work_dir, "force-app", "main", "default"), exist_ok=True)


def deploy_metadata(org: str, work_dir: str) -> bool:
    """Deploy metadata from a temp sfdx project directory."""
    result = run(
        f"sf project deploy start -o {org} --source-dir force-app",
        cwd=work_dir,
    )
    if result is None:
        print("  ERROR: Metadata deployment failed")
        return False
    print("  Deployed successfully")
    return True


# ---------------------------------------------------------------------------
# REST API helpers
# ---------------------------------------------------------------------------

def sf_rest_post(
    instance_url: str, access_token: str, path: str, body: dict,
    api_version: str = "v62.0",
) -> tuple[bool, dict | str]:
    """POST to the Salesforce REST API.

    Returns (True, response_dict) on success,
    (False, error_body_text) on HTTPError,
    (False, str(exception)) on other errors.
    """
    req = urllib.request.Request(
        f"{instance_url}/services/data/{api_version}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            return result.get("success", True), result
    except urllib.error.HTTPError as e:
        return False, e.read().decode()
    except Exception as e:
        return False, str(e)


def create_setup_entity_access(
    instance_url: str, access_token: str, parent_id: str, entity_id: str,
    api_version: str = "v62.0",
) -> bool:
    """Create a SetupEntityAccess record (idempotent -- handles DUPLICATE_VALUE)."""
    ok, result = sf_rest_post(
        instance_url, access_token,
        "/sobjects/SetupEntityAccess",
        {"ParentId": parent_id, "SetupEntityId": entity_id},
        api_version=api_version,
    )
    if ok:
        return True
    if isinstance(result, str) and "DUPLICATE_VALUE" in result:
        return True
    print(f"  FAILED: {str(result)[:200]}")
    return False


def assign_perm_set_to_user(
    instance_url: str, access_token: str, perm_set_id: str, user_id: str,
) -> bool:
    """Create a PermissionSetAssignment record (idempotent)."""
    ok, result = sf_rest_post(
        instance_url, access_token,
        "/sobjects/PermissionSetAssignment",
        {"AssigneeId": user_id, "PermissionSetId": perm_set_id},
    )
    if ok:
        return True
    if isinstance(result, str) and "DUPLICATE_VALUE" in result:
        return True
    print(f"  FAILED: {str(result)[:200]}")
    return False


# ---------------------------------------------------------------------------
# Azure CLI helpers (for SSO step)
# ---------------------------------------------------------------------------

def write_temp_json(data) -> str:
    """Write data as JSON to a temp file and return the file path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def graph_patch(object_id: str, body: dict):
    """PATCH a Microsoft Graph application resource."""
    body_file = write_temp_json(body)
    try:
        return run(
            f'az rest --method PATCH '
            f'--url "https://graph.microsoft.com/v1.0/applications/{object_id}" '
            f'--headers "Content-Type=application/json" '
            f'--body "@{body_file}"',
            parse_json=True,
        )
    finally:
        os.unlink(body_file)
