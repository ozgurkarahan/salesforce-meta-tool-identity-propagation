"""Consolidated Salesforce org setup after Dev Trial creation.

Orchestrates all SF setup phases in sequence after `sf org login web`:

  Step 1/4: SSO Federation        -- Entra App + Auth Provider + Apex handler
  Step 2/4: External Client App   -- ECA metadata + OAuth settings deployment
  Step 3/4: ECA Callback URL      -- Add ApiHub redirect URI to ECA's OAuth callbacks
  Step 4/4: Demo User + Test Data -- Custom profile (no Account delete) + user + sample data

Each step calls an existing standalone script via subprocess with pass-through
stdin/stdout, so interactive steps (SSO browser login) work correctly.

Prerequisites:
- az CLI logged in (for SSO + callback URL steps)
- sf CLI authenticated to the target org: sf org login web --alias <alias>
- azd env loaded (for callback URL config, optional for other steps)

Usage:
    python scripts/setup-sf-org.py --org <alias> --email <admin-email>
    python scripts/setup-sf-org.py --org <alias> --email <admin-email> --skip sso callback
    python scripts/setup-sf-org.py --org <alias> --email <admin-email> --only eca demo
"""

import argparse
import json
import os
import subprocess
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

# Script paths
SSO_SCRIPT = os.path.join(REPO_ROOT, ".claude", "scripts", "setup-salesforce-sso.py")
ECA_SCRIPT = os.path.join(SCRIPT_DIR, "setup-sf-external-client-app.py")
CALLBACK_SCRIPT = os.path.join(SCRIPT_DIR, "configure-sf-connected-app.py")
DEMO_USER_SCRIPT = os.path.join(SCRIPT_DIR, "setup-sf-demo-user.py")

STEPS = [
    ("sso", "SSO Federation"),
    ("eca", "External Client App"),
    ("callback", "ECA Callback URL Config"),
    ("demo", "Demo User + Test Data"),
]
STEP_KEYS = [s[0] for s in STEPS]


def run_step(step_num: int, total: int, label: str, cmd: str) -> bool:
    """Run a setup step with pass-through stdin/stdout for interactive scripts."""
    print()
    print("=" * 60)
    print(f"  Step {step_num}/{total}: {label}")
    print("=" * 60)
    print()

    result = subprocess.run(
        cmd, shell=True,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )

    if result.returncode != 0:
        print(f"\n  FAILED: Step {step_num} ({label}) exited with code {result.returncode}")
        return False

    print(f"\n  DONE: Step {step_num} ({label})")
    return True


def check_prerequisites(org: str):
    """Verify sf CLI is authenticated to the target org."""
    print("--- Prerequisites ---")

    result = subprocess.run(
        f"sf org display -o {org} --json",
        capture_output=True, text=True, shell=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )
    if result.returncode != 0:
        print(f"\n  ERROR: sf CLI not authenticated to org '{org}'")
        print(f"  Run: sf org login web --alias {org}")
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
        instance_url = data.get("result", {}).get("instanceUrl", "")
        username = data.get("result", {}).get("username", "")
        print(f"  SF org:      {instance_url}")
        print(f"  Admin user:  {username}")
    except (json.JSONDecodeError, KeyError):
        print("  SF org:      (authenticated)")


def main():
    parser = argparse.ArgumentParser(
        description="Consolidated Salesforce org setup after Dev Trial creation. "
        "Chains all setup phases: SSO -> ECA -> Callback URL -> Demo User."
    )
    parser.add_argument(
        "--org", required=True,
        help="Salesforce org alias (as authenticated with 'sf org login web')",
    )
    parser.add_argument(
        "--email", required=True,
        help="Admin email (used for ECA contact + demo user password reset)",
    )
    parser.add_argument(
        "--app-name", default="Identity_PoC_MCP",
        help="External Client App developer name (default: Identity_PoC_MCP)",
    )
    parser.add_argument(
        "--skip", nargs="+", choices=STEP_KEYS, default=[],
        help="Steps to skip (e.g., --skip sso callback)",
    )
    parser.add_argument(
        "--only", nargs="+", choices=STEP_KEYS, default=[],
        help="Run only these steps (e.g., --only eca demo)",
    )
    parser.add_argument(
        "--continue-on-error", action="store_true",
        help="Continue with remaining steps if a step fails (default: stop on failure)",
    )
    args = parser.parse_args()

    # Determine which steps to run
    if args.only:
        steps_to_run = set(args.only)
    else:
        steps_to_run = set(STEP_KEYS) - set(args.skip)

    print()
    print("#" * 60)
    print("#  Salesforce Org Setup -- Consolidated")
    print("#" * 60)
    print()
    print(f"  Org alias:  {args.org}")
    print(f"  Email:      {args.email}")
    print(f"  App name:   {args.app_name}")
    print()

    # Show step plan
    print("  Steps:")
    for key, label in STEPS:
        status = "RUN " if key in steps_to_run else "SKIP"
        print(f"    [{status}] {label}")
    print()

    check_prerequisites(args.org)

    total = len(steps_to_run)
    step_num = 0
    results = {}
    start_time = time.time()

    # Step 1: SSO Federation
    if "sso" in steps_to_run:
        step_num += 1
        if not os.path.exists(SSO_SCRIPT):
            print(f"\n  WARNING: SSO script not found at {SSO_SCRIPT}")
            print("  Skipping SSO setup (script not available)")
            results["sso"] = "SKIPPED"
        else:
            ok = run_step(
                step_num, total, "SSO Federation",
                f'python "{SSO_SCRIPT}"',
            )
            results["sso"] = "OK" if ok else "FAILED"
            if not ok and not args.continue_on_error:
                print("\n  Stopping (use --continue-on-error to proceed past failures)")
                _print_summary(results, steps_to_run, start_time)
                sys.exit(1)

    # Step 2: External Client App
    if "eca" in steps_to_run:
        step_num += 1
        ok = run_step(
            step_num, total, "External Client App",
            f'python "{ECA_SCRIPT}" '
            f"--org {args.org} --email {args.email} --app-name {args.app_name}",
        )
        results["eca"] = "OK" if ok else "FAILED"
        if not ok and not args.continue_on_error:
            print("\n  Stopping (use --continue-on-error to proceed past failures)")
            _print_summary(results, steps_to_run, start_time)
            sys.exit(1)

    # Step 3: ECA Callback URL Config
    if "callback" in steps_to_run:
        step_num += 1
        ok = run_step(
            step_num, total, "ECA Callback URL Config",
            f'python "{CALLBACK_SCRIPT}" '
            f"--app-name {args.app_name} --org {args.org}",
        )
        results["callback"] = "OK" if ok else "FAILED"
        if not ok and not args.continue_on_error:
            print("\n  Stopping (use --continue-on-error to proceed past failures)")
            _print_summary(results, steps_to_run, start_time)
            sys.exit(1)

    # Step 4: Demo User + Test Data
    if "demo" in steps_to_run:
        step_num += 1
        ok = run_step(
            step_num, total, "Demo User + Test Data",
            f'python "{DEMO_USER_SCRIPT}" '
            f"--org {args.org} --email {args.email}",
        )
        results["demo"] = "OK" if ok else "FAILED"

    _print_summary(results, steps_to_run, start_time)

    # Exit with error if any step failed
    if any(v == "FAILED" for v in results.values()):
        sys.exit(1)


def _print_summary(results: dict, steps_to_run: set, start_time: float):
    """Print final setup summary with results and manual steps."""
    elapsed = time.time() - start_time

    print()
    print("#" * 60)
    print("#  Setup Summary")
    print("#" * 60)
    print()

    for key, label in STEPS:
        if key in steps_to_run:
            status = results.get(key, "NOT RUN")
            if status == "OK":
                marker = " [OK]  "
            elif status == "FAILED":
                marker = " [FAIL]"
            else:
                marker = " [SKIP]"
        else:
            marker = " [SKIP]"
        print(f"  {marker} {label}")

    print()
    print(f"  Elapsed: {elapsed:.0f}s")

    print()
    print("  MANUAL STEPS REMAINING:")
    print("  1. Copy Consumer Secret from SF Setup:")
    print("     Setup > App Manager > Identity PoC MCP > Manage Consumer Details")
    print("     Then run: azd env set SF_CONNECTED_APP_CLIENT_SECRET <secret>")
    print('  2. Enable "Azure AD" on My Domain login page:')
    print("     Setup > My Domain > Authentication Configuration > Edit")
    print("  3. Run: azd up")
    print("  4. Complete OAuth consent in browser when prompted")
    print()


if __name__ == "__main__":
    main()
