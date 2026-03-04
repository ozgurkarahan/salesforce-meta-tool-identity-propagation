"""Test MCP Streamable HTTP SSE flow (GET listener + POST tool call).

Reproduces the pattern Foundry uses:
1. POST initialize -> 200 with session ID
2. Open GET SSE listener (background thread)
3. POST tool call -> 202 (or 200 with inline result)
4. Check if GET receives the SSE event

Usage:
    python scripts/test-mcp-sse.py --url <mcp-endpoint> --token <bearer-token>
    python scripts/test-mcp-sse.py --url <mcp-endpoint> --token-cmd "az account get-access-token ..."
"""

import argparse
import json
import subprocess
import sys
import threading
import time

import httpx


def get_token(args):
    if args.token:
        return args.token
    if args.token_cmd:
        result = subprocess.run(
            args.token_cmd, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        return result.stdout.strip()
    print("ERROR: --token or --token-cmd required")
    sys.exit(1)


def test_sse_flow(url: str, token: str, label: str):
    print(f"\n{'='*60}")
    print(f"  Testing: {label}")
    print(f"  URL: {url}")
    print(f"{'='*60}")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }

    # Step 1: Initialize
    print("\n[1] Initialize...")
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers=headers, json={
            "jsonrpc": "2.0", "method": "initialize", "id": 1,
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "sse-test", "version": "1.0"},
            },
        })
        print(f"    Status: {resp.status_code}")
        session_id = resp.headers.get("mcp-session-id", "")
        print(f"    Session: {session_id}")
        if resp.status_code != 200 or not session_id:
            print(f"    FAILED: {resp.text[:300]}")
            return False

    # Step 2: Test POST-based tool call (inline result)
    print("\n[2] POST tools/list (inline, same as curl)...")
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers={**headers, "mcp-session-id": session_id}, json={
            "jsonrpc": "2.0", "method": "tools/list", "id": 2,
        })
        print(f"    Status: {resp.status_code}")
        if resp.status_code == 200:
            body = resp.text
            if "tools" in body:
                print(f"    OK - got tools list ({len(body)} bytes)")
            else:
                print(f"    Response: {body[:300]}")
        elif resp.status_code == 202:
            print("    Got 202 - server wants to stream result via GET")
        else:
            print(f"    UNEXPECTED: {resp.text[:300]}")

    # Step 3: Test GET SSE listener (what Foundry uses)
    print("\n[3] GET SSE listener test...")
    # Create a new session for the GET test
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers=headers, json={
            "jsonrpc": "2.0", "method": "initialize", "id": 10,
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "sse-test-get", "version": "1.0"},
            },
        })
        session2 = resp.headers.get("mcp-session-id", "")
        print(f"    New session: {session2}")

    get_result = {"data": "", "status": None, "error": None, "headers": {}}

    def sse_listener():
        try:
            with httpx.Client(timeout=10) as client:
                with client.stream(
                    "GET", url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "text/event-stream",
                        "mcp-session-id": session2,
                    },
                ) as stream:
                    get_result["status"] = stream.status_code
                    get_result["headers"] = dict(stream.headers)
                    for chunk in stream.iter_text():
                        get_result["data"] += chunk
                        if len(get_result["data"]) > 100:
                            break
        except Exception as e:
            get_result["error"] = str(e)

    # Start GET listener in background
    t = threading.Thread(target=sse_listener, daemon=True)
    t.start()
    time.sleep(0.5)  # Give GET time to connect

    print(f"    GET status: {get_result['status']}")
    print(f"    GET content-type: {get_result['headers'].get('content-type', 'N/A')}")

    # Send tool call via POST
    print("\n[4] POST tool call while GET is listening...")
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers={
            **headers, "mcp-session-id": session2,
        }, json={
            "jsonrpc": "2.0", "method": "tools/list", "id": 11,
        })
        print(f"    POST status: {resp.status_code}")
        if resp.status_code == 202:
            print("    202 Accepted - result should arrive via GET SSE")
        elif resp.status_code == 200:
            print(f"    200 OK - inline result ({len(resp.text)} bytes)")

    # Wait for GET to receive data
    time.sleep(2)
    t.join(timeout=3)

    print(f"\n[5] GET SSE results:")
    print(f"    Status: {get_result['status']}")
    print(f"    Data received: {len(get_result['data'])} bytes")
    if get_result["error"]:
        print(f"    Error: {get_result['error']}")
    if get_result["data"]:
        print(f"    First 300 chars: {get_result['data'][:300]}")

    # Cleanup
    try:
        with httpx.Client(timeout=5) as client:
            client.delete(url, headers={
                "Authorization": f"Bearer {token}",
                "mcp-session-id": session2,
            })
    except Exception:
        pass

    success = get_result["status"] == 200 and len(get_result["data"]) > 0
    print(f"\n    Result: {'PASS' if success else 'FAIL'}")
    return success


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-cmd", default=None)
    parser.add_argument("--backend-url", default=None)
    parser.add_argument("--backend-token", default=None)
    parser.add_argument("--backend-token-cmd", default=None)
    args = parser.parse_args()

    token = get_token(args)

    # Test through APIM
    result1 = test_sse_flow(args.url, token, "Through APIM")

    # Test direct to backend (if provided)
    if args.backend_url:
        bt = args.backend_token
        if not bt and args.backend_token_cmd:
            r = subprocess.run(
                args.backend_token_cmd, shell=True, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
            )
            bt = r.stdout.strip()
        if bt:
            result2 = test_sse_flow(args.backend_url, bt, "Direct to backend")
        else:
            print("\nSkipping backend test (no token)")
            result2 = None
    else:
        result2 = None

    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  APIM:    {'PASS' if result1 else 'FAIL'}")
    if result2 is not None:
        print(f"  Backend: {'PASS' if result2 else 'FAIL'}")


if __name__ == "__main__":
    main()
