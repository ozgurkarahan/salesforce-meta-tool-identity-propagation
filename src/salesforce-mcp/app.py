"""Metadata-driven Salesforce MCP server.

Dynamically discovers Salesforce objects/fields and exposes query, search,
write, and approval tools via the Model Context Protocol (MCP).
"""

import json
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx

# --- Azure Monitor OpenTelemetry ---
_conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
if _conn_str:
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(connection_string=_conn_str)
    _root = logging.getLogger()
    _root.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _root.addHandler(_h)
    logging.getLogger("azure").setLevel(logging.WARNING)
    print("Azure Monitor OpenTelemetry configured for salesforce-mcp")
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from salesforce_client import SalesforceClient, _request_token

log = logging.getLogger("salesforce_mcp")

sf = SalesforceClient()

port = int(os.environ.get("PORT", "8000"))


@asynccontextmanager
async def lifespan(app):
    # Recreate httpx client on each ASGI startup. This is needed because
    # Container App revision updates trigger ASGI shutdown (closing the client
    # via sf.close()) then restart — but the module-level `sf` singleton
    # persists in Python's module cache with a closed httpx client.
    sf._client = httpx.AsyncClient(timeout=30.0)
    yield
    await sf.close()


mcp = FastMCP(
    "Salesforce Meta Tool - MCP Server",
    lifespan=lifespan,
    instructions="""\
Salesforce MCP server — discovers objects and fields dynamically via metadata APIs.

## Workflow
1. **Plan** — Tell the user what you intend to do before calling tools.
2. **list_objects** — Find the API name (use `name`, not `label`, for all subsequent calls).
3. **describe_object** — REQUIRED before create/update/upsert/delete.
   For read queries, skip if you already know the field names, or use mode="slim" if unsure.
4. **Execute** — soql_query, search_records, write_record, or process_approval.
5. **Summarize** — Present results in plain language. Do NOT dump raw JSON for large results.

## Conventions
- All API names are PascalCase: Account, OpportunityLineItem, Custom_Field__c.
- Field values use API name as key: {"Name": "Acme", "Industry": "Technology"}.
- Record IDs are 18-character alphanumeric strings.
- Common fields on standard objects: Id, Name, CreatedDate, OwnerId, LastModifiedDate.
  You may query these without calling describe_object first.

## Rules
- Do NOT guess field names — use describe_object (slim for reads, full for writes).
- On INVALID_FIELD or MALFORMED_QUERY: call describe_object(mode="full"), fix field names, retry.
- ALWAYS confirm with the user before delete or reject operations.
- Always include LIMIT in SOQL unless the user specifically requests all rows.
- Summarize large result sets in plain language — do not dump raw JSON.

## Error recovery
- **INSUFFICIENT_ACCESS** → User lacks permission. Explain which object/field and what permission is needed.
- **ENTITY_IS_DELETED** → Record was deleted. Inform the user.
- **UNABLE_TO_LOCK_ROW** → Concurrent edit. Wait a moment and retry once.
- On any error, explain the cause in plain language before retrying.
""",
    host="0.0.0.0",
    port=port,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})


def _sf_error_response(e: httpx.HTTPStatusError) -> str:
    """Extract Salesforce error details from an HTTP error response."""
    status = e.response.status_code
    try:
        body = e.response.json()
        if isinstance(body, list) and body:
            sf_err = body[0]
            return json.dumps({
                "success": False,
                "errorCode": sf_err.get("errorCode", "UNKNOWN"),
                "message": sf_err.get("message", str(e)),
                "fields": sf_err.get("fields", []),
                "httpStatus": status,
            })
    except Exception:
        pass
    return json.dumps({
        "success": False,
        "errorCode": "HTTP_ERROR",
        "message": str(e),
        "httpStatus": status,
    })


def _clean_attributes(obj):
    """Recursively remove Salesforce 'attributes' metadata from records."""
    if isinstance(obj, dict):
        obj.pop("attributes", None)
        for v in obj.values():
            _clean_attributes(v)
    elif isinstance(obj, list):
        for item in obj:
            _clean_attributes(item)


@mcp.tool()
async def list_objects(filter: str | None = None) -> str:
    """List available Salesforce objects with permission flags.

    A typical org has 1000+ objects. Always provide a filter to narrow results
    (e.g., "Account", "Order", "Case"). Without one, only the first 100
    alphabetically are returned. Use `name` (API name) for all subsequent calls.

    Args:
        filter: Case-insensitive filter on object name or label. Strongly recommended.

    Returns:
        JSON array (max 100) with name, label, queryable, createable, updateable, deletable.
    """
    log.info("tool=list_objects filter=%s", filter)
    t0 = time.monotonic()
    try:
        objects = await sf.describe_global()
    except httpx.HTTPStatusError as e:
        return _sf_error_response(e)

    if filter:
        f = filter.lower()
        objects = [
            o for o in objects
            if f in o["name"].lower() or f in o["label"].lower()
        ]

    result = objects[:100]
    log.info("tool=list_objects done count=%d elapsed=%.1fs", len(result), time.monotonic() - t0)
    return json.dumps(result)


@mcp.tool()
async def describe_object(object_name: str, mode: str = "slim") -> str:
    """Get field metadata for a Salesforce object.

    Args:
        object_name: API name (e.g., Account, Contact, Opportunity). Use `name` from list_objects.
        mode: "slim" (default) — field names, types, required flags. Use for building queries.
              "full" — includes picklistValues, referenceTo, childRelationships, externalId.
              Use "full" before create/update/upsert/delete.

    Returns:
        slim: JSON with name and fields (name, type, required).
        full: JSON with fields (name, label, type, required, externalId, picklistValues,
              referenceTo) and childRelationships.
    """
    slim = mode != "full"
    log.info("tool=describe_object object=%s mode=%s", object_name, mode)
    t0 = time.monotonic()
    try:
        result = await sf.describe_object(object_name, slim=slim)
    except httpx.HTTPStatusError as e:
        return _sf_error_response(e)
    log.info("tool=describe_object done elapsed=%.1fs", time.monotonic() - t0)
    return json.dumps(result)


@mcp.tool()
async def soql_query(query: str, max_records: int = 2000) -> str:
    """Execute a SOQL query with automatic pagination.

    Use for precise, structured lookups. Use search_records for full-text/fuzzy search.
    SOQL requires explicit field names (no SELECT *).

    Syntax:
        - Parent fields: `Account.Name` (dot notation via referenceTo).
        - Child subqueries: `(SELECT Id FROM Contacts)` (uses relationshipName, not object name).
        - Examples: "SELECT Id, Name FROM Account LIMIT 5",
          "SELECT Id, Account.Name FROM Contact LIMIT 5"

    Args:
        query: Complete SOQL string.
        max_records: Max records to return (default 2000, cap 50000). Auto-paginates.

    Returns:
        JSON with totalSize, records array, done flag. done=false means results were truncated.
    """
    log.info("tool=soql_query max_records=%d", max_records)
    t0 = time.monotonic()
    max_records = min(max_records, 50000)
    try:
        result = await sf.query(query)
        records = result.get("records", [])
        total_size = result.get("totalSize", len(records))

        while not result.get("done") and result.get("nextRecordsUrl") and len(records) < max_records:
            result = await sf.query_more(result["nextRecordsUrl"])
            records.extend(result.get("records", []))
    except httpx.HTTPStatusError as e:
        return _sf_error_response(e)

    _clean_attributes(records)

    log.info("tool=soql_query done total=%d returned=%d elapsed=%.1fs", total_size, len(records[:max_records]), time.monotonic() - t0)
    return json.dumps({
        "totalSize": total_size,
        "records": records[:max_records],
        "done": result.get("done", True) and len(records) <= max_records,
    })


@mcp.tool()
async def search_records(
    search_term: str,
    objects: str | None = None,
    limit: int = 20,
) -> str:
    """Full-text search across multiple Salesforce objects (SOSL).

    Use when the target object is unknown or for fuzzy/keyword search.
    Prefer soql_query for exact matches and WHERE clauses.

    Args:
        search_term: Plain text to search for (e.g., "Acme"). Special chars auto-escaped.
        objects: RETURNING clause — objects and fields to return.
            E.g., "Account(Name, Industry), Contact(FirstName, Email)".
            Omit to search all objects with default fields.
        limit: Max records to return (default 20, max 200).

    Returns:
        JSON with searchRecords array.
    """
    log.info("tool=search_records term=%s objects=%s", search_term, objects)
    t0 = time.monotonic()
    limit = min(limit, 200)

    # Escape SOSL reserved characters (backslash first to avoid double-escaping)
    escaped = search_term.replace("\\", "\\\\")
    for ch in '?&|!{}[]()^~*:"\'+-':
        escaped = escaped.replace(ch, f"\\{ch}")

    sosl = f"FIND {{{escaped}}} IN ALL FIELDS"
    if objects:
        sosl += f" RETURNING {objects}"
    sosl += f" LIMIT {limit}"

    try:
        result = await sf.search(sosl)
    except httpx.HTTPStatusError as e:
        return _sf_error_response(e)
    records = result.get("searchRecords", [])
    _clean_attributes(records)

    log.info("tool=search_records done count=%d elapsed=%.1fs", len(records), time.monotonic() - t0)
    return json.dumps({"searchRecords": records})


@mcp.tool()
async def write_record(
    object_name: str,
    operation: str,
    field_values: dict | None = None,
    record_id: str | None = None,
    external_id_field: str | None = None,
) -> str:
    """Create, update, upsert, or delete a Salesforce record.

    Operations:
        create  — field_values required (include all required fields).
        update  — record_id + field_values (partial update).
        upsert  — field_values + external_id_field. External ID value must be in field_values.
        delete  — record_id only.

    Args:
        object_name: API name (e.g., Account, Contact).
        operation: One of "create", "update", "upsert", "delete".
        field_values: API field names to values. E.g., {"Name": "Acme", "Industry": "Technology"}.
        record_id: 18-char Salesforce record ID. Required for update/delete.
        external_id_field: External ID field for upsert. Must have externalId: true in describe_object.

    Returns:
        JSON with success flag and details (id for create, created flag for upsert).
    """
    log.info("tool=write_record object=%s op=%s", object_name, operation)
    t0 = time.monotonic()
    op = operation.lower()
    valid_ops = ("create", "update", "upsert", "delete")
    if op not in valid_ops:
        return json.dumps({
            "success": False,
            "error": f"Invalid operation '{operation}'. Must be one of: {', '.join(valid_ops)}.",
        })

    # Validate required parameters per operation
    if op in ("create", "update", "upsert") and not field_values:
        return json.dumps({
            "success": False,
            "error": f"field_values is required for '{op}' operation.",
        })
    if op in ("update", "delete") and not record_id:
        return json.dumps({
            "success": False,
            "error": f"record_id is required for '{op}' operation.",
        })
    if op == "upsert" and not external_id_field:
        return json.dumps({
            "success": False,
            "error": "external_id_field is required for 'upsert' operation.",
        })

    try:
        # Validate field names for operations that send data
        desc = None
        if op in ("create", "update", "upsert") and field_values:
            desc = await sf.describe_object(object_name)
            valid_fields = {f["name"] for f in desc["fields"]}
            invalid = set(field_values.keys()) - valid_fields
            if invalid:
                return json.dumps({
                    "success": False,
                    "error": f"Invalid field names: {', '.join(sorted(invalid))}. Use describe_object to find valid field names.",
                })

        # Validate external ID field for upsert
        if op == "upsert":
            if not desc:
                desc = await sf.describe_object(object_name)
            ext_field_meta = next(
                (f for f in desc["fields"] if f["name"] == external_id_field), None
            )
            if not ext_field_meta:
                return json.dumps({
                    "success": False,
                    "error": f"Field '{external_id_field}' not found on {object_name}.",
                })
            if not ext_field_meta.get("externalId") and ext_field_meta.get("type") != "id":
                return json.dumps({
                    "success": False,
                    "error": f"Field '{external_id_field}' is not marked as an External ID on {object_name}. "
                             "Use describe_object to find fields with externalId: true.",
                })

        if op == "create":
            result = await sf.create_record(object_name, field_values)
        elif op == "update":
            result = await sf.update_record(object_name, record_id, field_values)
        elif op == "upsert":
            external_id_value = field_values.get(external_id_field, "")
            if not external_id_value:
                return json.dumps({
                    "success": False,
                    "error": f"field_values must include a value for the external ID field '{external_id_field}'.",
                })
            # Don't send the external ID field in the body — it's in the URL
            upsert_fields = {k: v for k, v in field_values.items() if k != external_id_field}
            result = await sf.upsert_record(
                object_name, external_id_field, str(external_id_value), upsert_fields
            )
        else:  # delete
            result = await sf.delete_record(object_name, record_id)
    except httpx.HTTPStatusError as e:
        return _sf_error_response(e)

    log.info("tool=write_record done elapsed=%.1fs", time.monotonic() - t0)
    return json.dumps(result)


@mcp.tool()
async def process_approval(
    action: str,
    record_id: str,
    comments: str | None = None,
) -> str:
    """Submit, approve, or reject a Salesforce approval request.

    For Approve/Reject, query ProcessInstanceWorkitem first to get the workitem ID.
    The record_id for Submit is the record itself; for Approve/Reject it is the
    ProcessInstanceWorkitem ID (not the record).

    Args:
        action: One of "Submit", "Approve", "Reject".
        record_id: For Submit — the record ID. For Approve/Reject — the ProcessInstanceWorkitem ID.
        comments: Optional comments for the approval action.

    Returns:
        JSON with success flag and approval result details.
    """
    log.info("tool=process_approval action=%s record=%s", action, record_id)
    t0 = time.monotonic()
    valid_actions = ("Submit", "Approve", "Reject")
    if action not in valid_actions:
        return json.dumps({
            "success": False,
            "error": f"Invalid action '{action}'. Must be one of: {', '.join(valid_actions)}.",
        })

    request = {
        "actionType": action,
        "contextId": record_id,
    }
    if comments:
        request["comments"] = comments

    try:
        result = await sf.process_approval([request])
    except httpx.HTTPStatusError as e:
        return _sf_error_response(e)

    # Flatten single-request response
    items = result.get("processResults", result.get("results", []))
    log.info("tool=process_approval done elapsed=%.1fs", time.monotonic() - t0)
    if len(items) == 1:
        return json.dumps(items[0])
    return json.dumps(result)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Extract Authorization: Bearer token and set it as the per-request context var.

    In production, APIM exchanges the user's Azure AD token for a Salesforce
    token and forwards it here. For local testing, set SF_ACCESS_TOKEN env var.
    """

    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        tok = _request_token.set(token)
        try:
            return await call_next(request)
        finally:
            _request_token.reset(tok)


if __name__ == "__main__":
    import uvicorn

    app = mcp.streamable_http_app()
    app.add_middleware(BearerTokenMiddleware)
    uvicorn.run(app, host="0.0.0.0", port=port)
