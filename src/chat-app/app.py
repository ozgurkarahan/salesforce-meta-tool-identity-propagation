"""Chat App backend — bridges browser MSAL auth to Foundry agent via Responses API.

Endpoints:
  GET  /health           — Health check
  GET  /api/config       — MSAL config (from env vars, no hardcoded values)
  POST /api/chat         — Send message to agent (OBO flow)
  POST /api/chat/approve — Approve MCP tool calls
  GET  /                 — Static SPA (index.html)
"""

import asyncio
import logging
import os
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

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
# Helpers
# ---------------------------------------------------------------------------

class UserTokenCredential:
    """TokenCredential that wraps a user-provided access token.

    The Foundry SDK's AIProjectClient needs a TokenCredential. This class
    wraps the user's MSAL access token so the agent calls carry the user's
    identity — enabling end-to-end identity propagation.
    """

    def __init__(self, token: str):
        self._token = token

    def get_token(self, *scopes, **kwargs):
        from azure.core.credentials import AccessToken
        return AccessToken(self._token, 0)


def _get_agent_client(access_token: str):
    """Create an AIProjectClient authenticated with the user's token."""
    from azure.ai.projects import AIProjectClient

    endpoint = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise HTTPException(status_code=500, detail="AI_FOUNDRY_PROJECT_ENDPOINT not configured")

    credential = UserTokenCredential(access_token)
    return AIProjectClient(endpoint=endpoint, credential=credential)


def _parse_output_items(output_items):
    """Parse Responses API output items into a structured result."""
    result = {
        "type": "text",
        "text": "",
        "approval_required": False,
        "approval_ids": [],
    }

    for item in output_items:
        item_type = getattr(item, "type", "unknown")

        if item_type == "mcp_approval_request":
            result["type"] = "approval_required"
            result["approval_required"] = True
            result["approval_ids"].append({
                "id": getattr(item, "id", ""),
                "name": getattr(item, "name", ""),
                "server_label": getattr(item, "server_label", ""),
                "arguments": getattr(item, "arguments", {}),
            })

        elif item_type == "message":
            content = getattr(item, "content", [])
            for c in content:
                if hasattr(c, "text"):
                    result["text"] += c.text

    return result


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


@app.post("/api/chat")
async def chat(request: Request):
    """Send a message to the Foundry agent via the Responses API."""
    body = await request.json()
    access_token = body.get("access_token")
    message = body.get("message", "")
    previous_response_id = body.get("previous_response_id")
    session_id = body.get("session_id", "unknown")
    request_id = str(uuid.uuid4())

    if not access_token:
        raise HTTPException(status_code=401, detail="access_token required")

    logger.info("chat_request request_id=%s session_id=%s", request_id, session_id)

    project_client = _get_agent_client(access_token)
    agent_name = os.environ.get("AGENT_NAME", "salesforce-assistant")

    openai_client = project_client.get_openai_client()

    try:
        kwargs = {
            "input": message,
            "extra_body": {"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        }

        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id

        response = await asyncio.wait_for(
            asyncio.to_thread(openai_client.responses.create, **kwargs),
            timeout=120,
        )

        output_items = getattr(response, "output", [])
        output_types = [getattr(item, "type", "unknown") for item in output_items]
        logger.info(
            "chat_output_items request_id=%s types=%s count=%d",
            request_id, output_types, len(output_items),
        )
        # Log any tool-related items for debugging (errors, mcp_list_changed, etc.)
        for item in output_items:
            item_type = getattr(item, "type", "unknown")
            if item_type not in ("message", "oauth_consent_request", "mcp_approval_request"):
                logger.info(
                    "chat_output_item request_id=%s type=%s item=%s",
                    request_id, item_type, str(item)[:500],
                )

        parsed = _parse_output_items(output_items)

        # Get text from output_text if not found in items
        if not parsed["text"]:
            parsed["text"] = getattr(response, "output_text", "") or ""

        logger.info(
            "chat_response request_id=%s foundry_response_id=%s type=%s text_preview=%s",
            request_id, response.id, parsed["type"], (parsed["text"] or "")[:200],
        )

        return {
            "response_id": response.id,
            "request_id": request_id,
            **parsed,
        }
    except asyncio.TimeoutError:
        logger.error("Agent call timed out request_id=%s", request_id)
        raise HTTPException(status_code=504, detail="Agent call timed out")
    except Exception as e:
        logger.exception("Agent call failed request_id=%s", request_id)
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        openai_client.close()


@app.post("/api/chat/approve")
async def chat_approve(request: Request):
    """Approve MCP tool calls and continue the conversation."""
    body = await request.json()
    access_token = body.get("access_token")
    previous_response_id = body.get("previous_response_id")
    approval_ids = body.get("approval_ids", [])

    if not access_token:
        raise HTTPException(status_code=401, detail="access_token required")
    if not previous_response_id:
        raise HTTPException(status_code=400, detail="previous_response_id required")

    project_client = _get_agent_client(access_token)
    agent_name = os.environ.get("AGENT_NAME", "salesforce-assistant")

    openai_client = project_client.get_openai_client()

    try:
        # Build approval input
        try:
            from openai.types.responses.response_input_param import McpApprovalResponse
            approval_input = [
                McpApprovalResponse(
                    type="mcp_approval_response",
                    approve=True,
                    approval_request_id=aid,
                )
                for aid in approval_ids
            ]
        except ImportError:
            approval_input = [
                {
                    "type": "mcp_approval_response",
                    "approve": True,
                    "approval_request_id": aid,
                }
                for aid in approval_ids
            ]

        response = await asyncio.wait_for(
            asyncio.to_thread(
                openai_client.responses.create,
                previous_response_id=previous_response_id,
                input=approval_input,
                extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
            ),
            timeout=120,
        )

        output_items = getattr(response, "output", [])
        parsed = _parse_output_items(output_items)

        if not parsed["text"]:
            parsed["text"] = getattr(response, "output_text", "") or ""

        return {
            "response_id": response.id,
            **parsed,
        }
    except asyncio.TimeoutError:
        logger.error("Approval call timed out")
        raise HTTPException(status_code=504, detail="Approval call timed out")
    except Exception as e:
        logger.exception("Approval call failed")
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        openai_client.close()


# ---------------------------------------------------------------------------
# Static files (SPA) — must be mounted after API routes
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory="static", html=True), name="static")
