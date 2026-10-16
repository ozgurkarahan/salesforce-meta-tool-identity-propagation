"""Shared Foundry SDK helpers for multi-channel agent access.

Used by both the web chat app and the Teams bot to call the Foundry agent.
Each channel acquires the user's Azure AD token independently, then uses
these helpers to interact with the Foundry Responses API.
"""

import asyncio
import logging
import os
import uuid

from azure.core.credentials import AccessToken

logger = logging.getLogger(__name__)


class UserTokenCredential:
    """TokenCredential that wraps a user-provided access token.

    The Foundry SDK's AIProjectClient needs a TokenCredential. This class
    wraps the user's Azure AD access token so the agent calls carry the
    user's identity -- enabling end-to-end identity propagation.
    """

    def __init__(self, token: str):
        self._token = token

    def get_token(self, *scopes, **kwargs):
        return AccessToken(self._token, 0)


def create_foundry_client(access_token: str, project_endpoint: str = None):
    """Create an AIProjectClient authenticated with the user's token.

    Args:
        access_token: User's Azure AD bearer token
        project_endpoint: Override for AI_FOUNDRY_PROJECT_ENDPOINT env var
    """
    from azure.ai.projects import AIProjectClient

    endpoint = project_endpoint or os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise ValueError("AI_FOUNDRY_PROJECT_ENDPOINT not configured")

    credential = UserTokenCredential(access_token)
    return AIProjectClient(endpoint=endpoint, credential=credential)


# MCP server label -> backend system. Direct MCP tools use these labels as
# server_label; through a Foundry Toolbox the call arrives as
# "<server_label>___<tool>" (triple underscore) under the toolbox's own label.
MCP_SYSTEMS = {
    "salesforce_mcp": "salesforce",
    "servicenow_mcp": "servicenow",
}


def tool_system(server_label: str, name: str):
    """Return (system, bare_tool_name); unknown labels map to 'unknown'."""
    label = server_label or ""
    tool = name or ""
    if "___" in tool:
        label, tool = tool.split("___", 1)
    return MCP_SYSTEMS.get(label, "unknown"), tool


def parse_output_items(output_items, request_id: str = ""):
    """Parse Responses API output items into a structured result.

    Returns:
        dict with keys: type, text, approval_required, approval_ids,
                        consent_required, consent, tool_calls
    """
    result = {
        "type": "text",
        "text": "",
        "approval_required": False,
        "approval_ids": [],
        "consent_required": False,
        "consent": [],
        "tool_calls": [],
    }

    for item in output_items:
        item_type = getattr(item, "type", "unknown")

        if item_type == "oauth_consent_request":
            # First call per user per OAuth2 connection: the user must sign in once,
            # then the same message is re-sent with previous_response_id.
            result["type"] = "consent_required"
            result["consent_required"] = True
            result["consent"].append({
                "server_label": getattr(item, "server_label", "") or "",
                "consent_link": getattr(item, "consent_link", "") or "",
            })
            logger.info(
                "oauth_consent_requested request_id=%s server=%s",
                request_id, getattr(item, "server_label", ""),
            )

        elif item_type == "mcp_approval_request":
            result["type"] = "approval_required"
            result["approval_required"] = True
            tool_name = getattr(item, "name", "")
            tool_args = getattr(item, "arguments", {})
            result["approval_ids"].append({
                "id": getattr(item, "id", ""),
                "name": tool_name,
                "server_label": getattr(item, "server_label", ""),
                "arguments": tool_args,
            })
            logger.info(
                "tool_approval_requested request_id=%s tool=%s server=%s args=%s",
                request_id, tool_name,
                getattr(item, "server_label", ""),
                str(tool_args)[:300],
            )

        elif item_type == "mcp_call":
            server_label = getattr(item, "server_label", "") or ""
            raw_name = getattr(item, "name", "") or ""
            system, tool_name = tool_system(server_label, raw_name)
            tool_args = getattr(item, "arguments", {})
            tool_error = getattr(item, "error", None)
            logger.info(
                "tool_call request_id=%s system=%s tool=%s server=%s args=%s error=%s",
                request_id, system, tool_name, server_label,
                str(tool_args)[:300],
                str(tool_error)[:200] if tool_error else None,
            )
            result["tool_calls"].append({
                "name": tool_name,
                "system": system,
                "server_label": server_label,
                "arguments": tool_args,
                "error": str(tool_error) if tool_error else None,
                "output": getattr(item, "output", None),
            })

        elif item_type == "message":
            content = getattr(item, "content", [])
            for c in content:
                if hasattr(c, "text"):
                    result["text"] += c.text

    return result


async def call_agent(access_token: str, message: str, previous_response_id: str = None,
                     agent_name: str = None, project_endpoint: str = None,
                     timeout: float = 120) -> dict:
    """Send a message to the Foundry agent and return the parsed response.

    Args:
        access_token: User's Azure AD bearer token
        message: User message text
        previous_response_id: For multi-turn conversations
        agent_name: Agent name (default: from AGENT_NAME env var)
        project_endpoint: Override for AI_FOUNDRY_PROJECT_ENDPOINT env var
        timeout: Request timeout in seconds

    Returns:
        dict with keys: response_id, request_id, type, text,
                        approval_required, approval_ids
    """
    request_id = str(uuid.uuid4())
    agent_name = agent_name or os.environ.get("AGENT_NAME", "salesforce-assistant")

    logger.info("agent_call request_id=%s agent=%s", request_id, agent_name)

    project_client = create_foundry_client(access_token, project_endpoint)
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
            timeout=timeout,
        )

        output_items = getattr(response, "output", [])
        output_types = [getattr(item, "type", "unknown") for item in output_items]
        logger.info(
            "agent_output request_id=%s types=%s count=%d",
            request_id, output_types, len(output_items),
        )

        parsed = parse_output_items(output_items, request_id)

        if not parsed["text"]:
            parsed["text"] = getattr(response, "output_text", "") or ""

        # Extract token usage if available
        usage = getattr(response, "usage", None)
        usage_dict = None
        if usage:
            usage_dict = {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
            }
            logger.info(
                "agent_usage request_id=%s input=%d output=%d total=%d",
                request_id, usage_dict["input_tokens"],
                usage_dict["output_tokens"], usage_dict["total_tokens"],
            )

        logger.info(
            "agent_response request_id=%s response_id=%s type=%s text_preview=%s",
            request_id, response.id, parsed["type"], (parsed["text"] or "")[:200],
        )

        return {
            "response_id": response.id,
            "request_id": request_id,
            **parsed,
            **({"usage": usage_dict} if usage_dict else {}),
        }
    finally:
        openai_client.close()


async def approve_tools(access_token: str, previous_response_id: str,
                        approval_ids: list, approve: bool = True,
                        agent_name: str = None, project_endpoint: str = None,
                        timeout: float = 120) -> dict:
    """Approve or deny MCP tool calls and continue the conversation.

    Args:
        access_token: User's Azure AD bearer token
        previous_response_id: Response ID containing the approval request
        approval_ids: List of approval request IDs to approve/deny
        approve: True to approve, False to deny
        agent_name: Agent name (default: from AGENT_NAME env var)
        project_endpoint: Override for AI_FOUNDRY_PROJECT_ENDPOINT env var
        timeout: Request timeout in seconds

    Returns:
        dict with keys: response_id, type, text, approval_required, approval_ids, tool_calls
    """
    agent_name = agent_name or os.environ.get("AGENT_NAME", "salesforce-assistant")

    project_client = create_foundry_client(access_token, project_endpoint)
    openai_client = project_client.get_openai_client()

    try:
        try:
            from openai.types.responses.response_input_param import McpApprovalResponse
            approval_input = [
                McpApprovalResponse(
                    type="mcp_approval_response",
                    approve=approve,
                    approval_request_id=aid,
                )
                for aid in approval_ids
            ]
        except ImportError:
            approval_input = [
                {
                    "type": "mcp_approval_response",
                    "approve": approve,
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
            timeout=timeout,
        )

        output_items = getattr(response, "output", [])
        parsed = parse_output_items(output_items, request_id="approve")

        if not parsed["text"]:
            parsed["text"] = getattr(response, "output_text", "") or ""

        usage = getattr(response, "usage", None)
        usage_dict = None
        if usage:
            usage_dict = {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
            }

        return {
            "response_id": response.id,
            **parsed,
            **({"usage": usage_dict} if usage_dict else {}),
        }
    finally:
        openai_client.close()
