"""Chat App backend — bridges browser MSAL auth to Foundry agent via Responses API.

Endpoints:
  GET  /health           — Health check
  GET  /api/config       — MSAL config (from env vars, no hardcoded values)
  GET  /api/agents       — Available agents grouped by project
  POST /api/chat         — Send message to agent (OBO flow)
  POST /api/chat/approve — Approve MCP tool calls
  GET  /api/debug/logs   — SSE stream of App Insights logs for a session
  POST /api/messages     — Bot Framework endpoint for Teams
  GET  /                 — Static SPA (index.html)
"""

import asyncio
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.foundry_helpers import call_agent, approve_tools, parse_output_items  # noqa: E402

# --- Azure Monitor OpenTelemetry ---
_conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
if _conn_str:
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(connection_string=_conn_str)
    # OTel adds handler at WARNING level; lower to INFO for app logs.
    # Add StreamHandler so logs also appear in container logs.
    _root = logging.getLogger()
    _root.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _root.addHandler(_h)
    # Suppress verbose Azure SDK HTTP logging
    logging.getLogger("azure").setLevel(logging.WARNING)
    print("Azure Monitor OpenTelemetry configured for chat-app")
else:
    logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

app = FastAPI(title="Chat App", docs_url=None, redoc_url=None)

# Explicit instrumentation — auto-discovery may fail with vendored deps (pip --target)
if _conn_str:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/api/config")
async def config():
    """Return MSAL config from environment variables."""
    client_id = os.environ.get("CHAT_APP_ENTRA_CLIENT_ID", "")
    tenant_id = os.environ.get("TENANT_ID", "")

    if not client_id or not tenant_id:
        raise HTTPException(
            status_code=500,
            detail="CHAT_APP_ENTRA_CLIENT_ID or TENANT_ID not configured",
        )

    return {
        "clientId": client_id,
        "authority": f"https://login.microsoftonline.com/{tenant_id}",
        "scopes": ["https://ai.azure.com/.default"],
        "appInsightsConnectionString": os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", ""),
    }


import time as _time
import httpx

# Per-user cache: user_oid -> {data, expires}
_agents_cache_by_user = {}
_CACHE_TTL = 300  # 5 minutes


async def _discover_foundry_projects(arm_token: str):
    """Discover all AI Foundry projects the user has access to via Azure Resource Graph.

    Uses the user's ARM token to query Resource Graph — each user sees only
    the projects they have permissions on.
    """
    query = {
        "query": (
            "Resources "
            "| where type == 'microsoft.cognitiveservices/accounts/projects' "
            "| where properties.provisioningState == 'Succeeded' "
            "| project name, properties.displayName, properties.endpoints"
        ),
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01",
            headers={
                "Authorization": f"Bearer {arm_token}",
                "Content-Type": "application/json",
            },
            json=query,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

    projects = []
    for row in data.get("data", []):
        display_name = row.get("properties_displayName") or row.get("name", "")
        endpoints = row.get("properties_endpoints") or {}
        endpoint = endpoints.get("AI Foundry API", "")
        if endpoint:
            projects.append({"name": display_name, "endpoint": endpoint})

    return projects


async def _list_agents_for_project(endpoint: str):
    """List agents from a single Foundry project using managed identity."""
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=endpoint, credential=credential)
    try:
        raw = await asyncio.to_thread(lambda: list(client.agents.list()))
        return [
            {"name": a.name, "label": a.name.replace("-", " ").title()}
            for a in raw
        ]
    finally:
        client.close()


async def _discover_all_agents(arm_token: str, cache_key: str):
    """Full dynamic discovery: Resource Graph for projects, SDK for agents per project."""
    now = _time.time()
    cached = _agents_cache_by_user.get(cache_key)
    if cached and now < cached["expires"]:
        return cached["data"]

    # Step 1: Discover projects the user can see
    projects = await _discover_foundry_projects(arm_token)
    if not projects:
        logger.info("No Foundry projects found via Resource Graph")
        return None

    logger.info("Discovered %d Foundry projects: %s", len(projects),
                [p["name"] for p in projects])

    # Step 2: List agents in each project (using managed identity)
    result = []
    for proj in projects:
        endpoint = proj["endpoint"]
        name = proj["name"]
        try:
            agents = await _list_agents_for_project(endpoint)
            if agents:
                result.append({
                    "project": name,
                    "project_endpoint": endpoint,
                    "agents": agents,
                })
                logger.info("  Project '%s': %d agents", name, len(agents))
        except Exception as e:
            logger.warning("  Failed to list agents for '%s': %s", name, e)

    if result:
        _agents_cache_by_user[cache_key] = {"data": result, "expires": now + _CACHE_TTL}

    return result or None


@app.get("/api/agents")
async def agents(request: Request):
    """Discover Foundry projects + agents dynamically.

    Requires the user's access token (passed as Authorization header).
    Uses Azure Resource Graph (user's ARM token) to find projects,
    then AIProjectClient (managed identity) to list agents per project.

    Fallback chain: dynamic → AGENTS_CONFIG → AGENT_NAME.
    """
    auth = request.headers.get("Authorization", "")
    if not auth:
        raise HTTPException(status_code=401, detail="Sign in required")

    # Extract user token — the frontend sends a Foundry-scoped token (ai.azure.com),
    # but Resource Graph needs an ARM-scoped token (management.azure.com).
    # We'll try with the token we have; if it fails, fall back to managed identity discovery.
    ai_token = auth.removeprefix("Bearer ").strip()

    # Try to get an ARM token from the user via the backend's managed identity + OBO,
    # but that's complex. Instead: use managed identity for Resource Graph too,
    # and cache per-deployment (not per-user). User auth gates access but discovery
    # uses managed identity which has Reader on the subscription.
    try:
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential()
        arm_token_obj = credential.get_token("https://management.azure.com/.default")
        arm_token = arm_token_obj.token

        result = await _discover_all_agents(arm_token, cache_key="global")
        if result:
            return result
    except Exception as e:
        logger.warning("Dynamic discovery failed: %s", e)

    # Fallback: static AGENTS_CONFIG
    raw = os.environ.get("AGENTS_CONFIG", "")
    if raw:
        try:
            config = json.loads(raw)
            if config:
                return config
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid AGENTS_CONFIG JSON")

    # Final fallback: single agent
    endpoint = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
    return [{
        "project": "default",
        "project_endpoint": endpoint,
        "agents": [{
            "name": os.environ.get("AGENT_NAME", "salesforce-assistant"),
            "label": os.environ.get("AGENT_NAME", "Salesforce Assistant").replace("-", " ").title(),
        }],
    }]


@app.post("/api/chat")
async def chat(request: Request):
    """Send a message to the Foundry agent via the Responses API."""
    body = await request.json()
    access_token = body.get("access_token")
    message = body.get("message", "")
    previous_response_id = body.get("previous_response_id")
    session_id = body.get("session_id", "unknown")
    agent_name = body.get("agent_name")
    project_endpoint = body.get("project_endpoint")

    if not access_token:
        raise HTTPException(status_code=401, detail="access_token required")

    logger.info("chat_request session_id=%s agent=%s", session_id, agent_name or "default")

    try:
        result = await call_agent(
            access_token=access_token,
            message=message,
            previous_response_id=previous_response_id,
            agent_name=agent_name,
            project_endpoint=project_endpoint,
        )
        return result
    except asyncio.TimeoutError:
        logger.error("Agent call timed out session_id=%s", session_id)
        raise HTTPException(status_code=504, detail="Agent call timed out")
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("Agent call failed session_id=%s", session_id)
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/chat/approve")
async def chat_approve(request: Request):
    """Approve MCP tool calls and continue the conversation."""
    body = await request.json()
    access_token = body.get("access_token")
    previous_response_id = body.get("previous_response_id")
    approval_id_list = body.get("approval_ids", [])
    approve = body.get("approve", True)
    agent_name = body.get("agent_name")
    project_endpoint = body.get("project_endpoint")

    if not access_token:
        raise HTTPException(status_code=401, detail="access_token required")
    if not previous_response_id:
        raise HTTPException(status_code=400, detail="previous_response_id required")

    try:
        result = await approve_tools(
            access_token=access_token,
            previous_response_id=previous_response_id,
            approval_ids=approval_id_list,
            approve=approve,
            agent_name=agent_name,
            project_endpoint=project_endpoint,
        )
        return result
    except asyncio.TimeoutError:
        logger.error("Approval call timed out")
        raise HTTPException(status_code=504, detail="Approval call timed out")
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("Approval call failed")
        raise HTTPException(status_code=502, detail=str(e))


# ---------------------------------------------------------------------------
# Debug Log Tail — SSE endpoint (Phase 3)
# ---------------------------------------------------------------------------

_log_analytics_workspace_id = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID", "")


async def _query_log_analytics(client, workspace_id: str, session_id: str, since: datetime):
    """Query Log Analytics for traces related to a session, using request_id correlation."""
    from azure.monitor.query import LogsQueryStatus

    since_str = since.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # session_id is pre-validated as a UUID — safe for KQL string interpolation.
    # OTel Python logger writes session_id/request_id into Message text (not Properties),
    # so we correlate via OperationId from traces containing the session_id.
    kql = f"""
    let sid = "{session_id}";
    let opIds = AppTraces
        | where TimeGenerated > datetime({since_str})
        | where Message contains sid
        | distinct OperationId;
    union AppTraces, AppRequests, AppDependencies
    | where TimeGenerated > datetime({since_str})
    | where OperationId in (opIds)
    | order by TimeGenerated asc
    | project timestamp=TimeGenerated, source=AppRoleName, level=SeverityLevel,
        message=coalesce(Message, Name)
    """

    try:
        response = await asyncio.to_thread(
            client.query_workspace,
            workspace_id,
            kql,
            timespan=timedelta(hours=1),
        )

        if response.status != LogsQueryStatus.SUCCESS:
            logger.warning("Log query partial/failed: %s", response.status)
            return []

        results = []
        for table in response.tables:
            for row in table.rows:
                # Columns: timestamp, source, level, message
                results.append({
                    "timestamp": row[0].isoformat() if row[0] else None,
                    "source": row[1] or "unknown",
                    "level": row[2],
                    "message": row[3] or "",
                })
        return results

    except Exception as e:
        logger.warning("Log Analytics query failed: %s", e)
        return []


@app.get("/api/debug/logs")
async def debug_logs(session_id: str, request: Request):
    """SSE stream of App Insights logs correlated to a session."""
    # Validate session_id is a UUID to prevent KQL injection
    if not _UUID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id format")

    workspace_id = _log_analytics_workspace_id
    if not workspace_id:
        raise HTTPException(status_code=503, detail="LOG_ANALYTICS_WORKSPACE_ID not configured")

    async def stream():
        # Create credential and client once per SSE connection, not per poll
        try:
            from azure.identity import DefaultAzureCredential
            from azure.monitor.query import LogsQueryClient
        except ImportError:
            yield f"data: {json.dumps({'message': 'azure-monitor-query not installed'})}\n\n"
            return

        credential = DefaultAzureCredential()
        client = LogsQueryClient(credential)
        try:
            last_ts = datetime.now(timezone.utc) - timedelta(seconds=30)
            while True:
                if await request.is_disconnected():
                    break

                logs = await _query_log_analytics(client, workspace_id, session_id, last_ts)
                for log in logs:
                    yield f"data: {json.dumps(log)}\n\n"
                    if log.get("timestamp"):
                        try:
                            log_ts = datetime.fromisoformat(log["timestamp"].replace("Z", "+00:00"))
                            if log_ts > last_ts:
                                last_ts = log_ts
                        except (ValueError, TypeError):
                            pass

                await asyncio.sleep(4)
        finally:
            client.close()

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/debug/logs/snapshot")
async def debug_logs_snapshot(session_id: str, since: str = None):
    """One-shot fetch of App Insights logs for a session (non-streaming)."""
    if not _UUID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id format")

    workspace_id = _log_analytics_workspace_id
    if not workspace_id:
        raise HTTPException(status_code=503, detail="LOG_ANALYTICS_WORKSPACE_ID not configured")

    try:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient
    except ImportError:
        raise HTTPException(status_code=503, detail="azure-monitor-query not installed")

    since_dt = datetime.now(timezone.utc) - timedelta(minutes=30)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    credential = DefaultAzureCredential()
    client = LogsQueryClient(credential)
    try:
        return await _query_log_analytics(client, workspace_id, session_id, since_dt)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Teams Bot Framework endpoint (Phase 4)
# ---------------------------------------------------------------------------

# Bot Framework adapter (lazy-initialized)
_bot_adapter = None
_bot_app_id = os.environ.get("AGENT_BOT_MSA_APP_ID", "")
_bot_tenant_id = os.environ.get("TENANT_ID", "")

# Per-conversation state: conversation_id -> previous_response_id
_conversation_state = {}
# SSO token store: conversation_id -> access_token
_sso_tokens = {}


def _get_bot_adapter():
    """Lazy-initialize Bot Framework adapter."""
    global _bot_adapter
    if _bot_adapter is not None:
        return _bot_adapter

    try:
        from botbuilder.core import (
            BotFrameworkAdapter,
            BotFrameworkAdapterSettings,
        )
    except ImportError:
        logger.warning("botbuilder-core not installed — Teams endpoint unavailable")
        return None

    # NOTE: Empty app_password with single-tenant managed identity bot.
    # Bot Framework JWT verification relies on channel_auth_tenant for
    # single-tenant validation. The Container App is also behind Azure
    # Container Apps ingress, providing network-level protection.
    settings = BotFrameworkAdapterSettings(
        app_id=_bot_app_id,
        app_password="",
        channel_auth_tenant=_bot_tenant_id,
    )
    _bot_adapter = BotFrameworkAdapter(settings)
    return _bot_adapter


def _build_adaptive_card_for_tool_calls(tool_calls):
    """Build an Adaptive Card attachment showing tool call results."""
    body = []

    for tc in tool_calls:
        is_error = bool(tc.get("error"))
        status_color = "attention" if is_error else "good"
        status_text = "Error" if is_error else "Success"

        # Tool name header
        body.append({
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [{
                        "type": "TextBlock",
                        "text": tc.get("name", "unknown"),
                        "weight": "Bolder",
                        "fontType": "Monospace",
                        "size": "Small",
                    }],
                },
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [{
                        "type": "TextBlock",
                        "text": status_text,
                        "color": status_color,
                        "weight": "Bolder",
                        "size": "Small",
                    }],
                },
            ],
        })

        # Arguments (collapsed by default)
        args = tc.get("arguments")
        if args:
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
                args_str = json.dumps(parsed, indent=2)
            except (json.JSONDecodeError, TypeError):
                args_str = str(args)

            body.append({
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.ShowCard",
                    "title": "Arguments",
                    "card": {
                        "type": "AdaptiveCard",
                        "body": [{
                            "type": "TextBlock",
                            "text": args_str[:500],
                            "fontType": "Monospace",
                            "size": "Small",
                            "wrap": True,
                        }],
                    },
                }],
            })

        # Result preview
        output = tc.get("error") or tc.get("output")
        if output:
            output_str = output if isinstance(output, str) else json.dumps(output)
            preview = output_str[:300] + ("..." if len(output_str) > 300 else "")
            body.append({
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.ShowCard",
                    "title": "Show Result",
                    "card": {
                        "type": "AdaptiveCard",
                        "body": [{
                            "type": "TextBlock",
                            "text": preview,
                            "fontType": "Monospace",
                            "size": "Small",
                            "wrap": True,
                        }],
                    },
                }],
            })

        # Separator between tool calls
        body.append({"type": "TextBlock", "text": " ", "spacing": "Small"})

    return {
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.5",
            "body": body,
        },
    }


def _build_approval_card(approval_ids):
    """Build an Adaptive Card for tool approval requests."""
    body = [{
        "type": "TextBlock",
        "text": "Tool Approval Required",
        "weight": "Bolder",
        "color": "Warning",
        "size": "Medium",
    }]

    for a in approval_ids:
        body.append({
            "type": "TextBlock",
            "text": a.get("name", "unknown"),
            "fontType": "Monospace",
            "weight": "Bolder",
            "size": "Small",
        })
        args = a.get("arguments")
        if args:
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
                args_str = json.dumps(parsed, indent=2)
            except (json.JSONDecodeError, TypeError):
                args_str = str(args)
            body.append({
                "type": "TextBlock",
                "text": args_str[:400],
                "fontType": "Monospace",
                "size": "Small",
                "wrap": True,
            })

    return {
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.5",
            "body": body,
            "actions": [
                {
                    "type": "Action.Submit",
                    "title": "Approve",
                    "data": {
                        "action": "approve",
                        "approval_ids": [a.get("id") for a in approval_ids],
                    },
                    "style": "positive",
                },
                {
                    "type": "Action.Submit",
                    "title": "Deny",
                    "data": {
                        "action": "deny",
                        "approval_ids": [a.get("id") for a in approval_ids],
                    },
                    "style": "destructive",
                },
            ],
        },
    }


@app.post("/api/messages")
async def bot_messages(request: Request):
    """Bot Framework endpoint — receives Activities from Teams via Bot Service."""
    adapter = _get_bot_adapter()
    if adapter is None:
        raise HTTPException(status_code=503, detail="Bot Framework not available")

    try:
        from botbuilder.core import TurnContext
        from botbuilder.schema import Activity, ActivityTypes
    except ImportError:
        raise HTTPException(status_code=503, detail="botbuilder-core not installed")

    body = await request.json()
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("Authorization", "")

    async def _on_turn(turn_context: TurnContext):
        """Handle each incoming Activity."""
        if turn_context.activity.type == ActivityTypes.message:
            await _handle_message(turn_context)
        elif turn_context.activity.type == ActivityTypes.invoke:
            await _handle_invoke(turn_context)
        elif (
            turn_context.activity.type == ActivityTypes.event
            and turn_context.activity.name == "tokens/response"
        ):
            # SSO token response — extract and store
            await _handle_sso_token(turn_context)

    try:
        response = await adapter.process_activity(activity, auth_header, _on_turn)
        if response:
            return response.body
        return {}
    except Exception as e:
        logger.exception("Bot activity processing failed")
        raise HTTPException(status_code=500, detail=str(e))


async def _handle_message(turn_context):
    """Handle a text message from Teams."""
    from botbuilder.schema import Activity, ActivityTypes

    conversation_id = turn_context.activity.conversation.id
    user_message = turn_context.activity.text or ""

    # Check for approval action (from Adaptive Card submit)
    if turn_context.activity.value:
        await _handle_approval_action(turn_context)
        return

    # Try SSO token acquisition
    access_token = await _acquire_teams_token(turn_context)
    if not access_token:
        # Send OAuth card to trigger SSO
        await turn_context.send_activity(
            Activity(
                type=ActivityTypes.message,
                text="Please sign in to connect your identity.",
            )
        )
        return

    # Send typing indicator
    await turn_context.send_activity(Activity(type=ActivityTypes.typing))

    # Get previous response ID for conversation continuity
    previous_response_id = _conversation_state.get(conversation_id)

    try:
        result = await call_agent(
            access_token=access_token,
            message=user_message,
            previous_response_id=previous_response_id,
        )

        # Store response ID for multi-turn
        _conversation_state[conversation_id] = result.get("response_id")

        # Build response
        attachments = []

        # Add tool call Adaptive Card if there were tool calls
        tool_calls = result.get("tool_calls", [])
        if tool_calls:
            attachments.append(_build_adaptive_card_for_tool_calls(tool_calls))

        # First call per OAuth2 connection: user must consent once, then resend
        if result.get("consent_required"):
            links = [c.get("consent_link", "") for c in result.get("consent", [])
                     if (c.get("consent_link") or "").startswith("https://")]
            lines = "\n".join(f"- [Sign in]({link})" for link in links)
            await turn_context.send_activity(
                Activity(
                    type=ActivityTypes.message,
                    text=("One-time sign-in needed so the agent can act as you:\n"
                          f"{lines}\n\nAfter signing in, send your message again."),
                )
            )
            return

        # Check for approval request
        if result.get("approval_required"):
            approval_card = _build_approval_card(result.get("approval_ids", []))
            attachments.append(approval_card)
            await turn_context.send_activity(
                Activity(
                    type=ActivityTypes.message,
                    text="The agent needs your approval to proceed:",
                    attachments=attachments,
                )
            )
            return

        # Send text response with tool cards
        text = result.get("text", "")
        if text or attachments:
            await turn_context.send_activity(
                Activity(
                    type=ActivityTypes.message,
                    text=text or "(no text response)",
                    attachments=attachments if attachments else None,
                )
            )
        else:
            await turn_context.send_activity(
                Activity(
                    type=ActivityTypes.message,
                    text="Agent returned no response.",
                )
            )

    except asyncio.TimeoutError:
        await turn_context.send_activity(
            Activity(type=ActivityTypes.message, text="Agent call timed out. Please try again.")
        )
    except Exception as e:
        logger.exception("Teams agent call failed")
        await turn_context.send_activity(
            Activity(type=ActivityTypes.message, text=f"Error: {e}")
        )


async def _handle_approval_action(turn_context):
    """Handle Adaptive Card submit action for approval/denial."""
    from botbuilder.schema import Activity, ActivityTypes

    value = turn_context.activity.value or {}
    action = value.get("action")
    approval_ids = value.get("approval_ids", [])
    conversation_id = turn_context.activity.conversation.id
    approve = action == "approve"

    access_token = await _acquire_teams_token(turn_context)
    if not access_token:
        await turn_context.send_activity(
            Activity(type=ActivityTypes.message, text="Session expired. Please sign in again.")
        )
        return

    previous_response_id = _conversation_state.get(conversation_id)
    if not previous_response_id:
        await turn_context.send_activity(
            Activity(type=ActivityTypes.message, text="No pending conversation found.")
        )
        return

    try:
        result = await approve_tools(
            access_token=access_token,
            previous_response_id=previous_response_id,
            approval_ids=approval_ids,
            approve=approve,
        )

        _conversation_state[conversation_id] = result.get("response_id")

        text = result.get("text", "")
        decision = "Approved" if approve else "Denied"
        attachments = []
        tool_calls = result.get("tool_calls", [])
        if tool_calls:
            attachments.append(_build_adaptive_card_for_tool_calls(tool_calls))

        await turn_context.send_activity(
            Activity(
                type=ActivityTypes.message,
                text=f"({decision}) {text}" if text else f"Tools {decision.lower()}.",
                attachments=attachments if attachments else None,
            )
        )
    except Exception as e:
        logger.exception("Teams approval failed")
        await turn_context.send_activity(
            Activity(type=ActivityTypes.message, text=f"Approval error: {e}")
        )


async def _handle_invoke(turn_context):
    """Handle invoke activities (signin/tokenExchange for SSO)."""
    from botbuilder.schema import Activity

    if turn_context.activity.name == "signin/tokenExchange":
        # SSO token exchange — extract user token
        token_data = turn_context.activity.value or {}
        token = token_data.get("token")
        if token:
            conversation_id = turn_context.activity.conversation.id
            _sso_tokens[conversation_id] = token
            logger.info("SSO token received for conversation %s", conversation_id)
            # Acknowledge the invoke
            await turn_context.send_activity(
                Activity(type="invokeResponse", value={"status": 200, "body": {}})
            )


async def _handle_sso_token(turn_context):
    """Handle tokens/response event (fallback SSO)."""
    token_data = turn_context.activity.value or {}
    token = token_data.get("token")
    if token:
        conversation_id = turn_context.activity.conversation.id
        _sso_tokens[conversation_id] = token


async def _acquire_teams_token(turn_context):
    """Try to get the user's Azure AD token from SSO or cached state."""
    conversation_id = turn_context.activity.conversation.id

    # Check SSO token cache
    token = _sso_tokens.get(conversation_id)
    if token:
        return token

    # Check if the activity itself carries a token (tokenExchange)
    if turn_context.activity.value and isinstance(turn_context.activity.value, dict):
        token = turn_context.activity.value.get("token")
        if token:
            _sso_tokens[conversation_id] = token
            return token

    # Try OAuthPrompt-style token acquisition via Bot Framework
    try:
        from botbuilder.core import UserTokenProvider
        if isinstance(turn_context.adapter, UserTokenProvider):
            token_response = await turn_context.adapter.get_user_token(
                turn_context,
                os.environ.get("BOT_SSO_CONNECTION_NAME", ""),
                None,
            )
            if token_response and token_response.token:
                _sso_tokens[conversation_id] = token_response.token
                return token_response.token
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Static files (SPA) — must be mounted after API routes
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory="static", html=True), name="static")
