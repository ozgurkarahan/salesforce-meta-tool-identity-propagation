"""Metadata-driven Salesforce MCP server.

Dynamically discovers Salesforce objects/fields and exposes query, search,
write, and approval tools via the Model Context Protocol (MCP).
"""

import json
import logging
import os

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

from salesforce_client import SalesforceClient, _request_token

sf = SalesforceClient()

port = int(os.environ.get("PORT", "8000"))

mcp = FastMCP(
    "Salesforce Meta Tool - MCP Server",
    instructions="""\
Salesforce MCP server — dynamically discovers objects and fields via Salesforce metadata APIs.

## Recommended workflow
1. **list_objects** — Find the object API name (always use filter — 1000+ objects in a typical org).
2. **describe_object** — Get field names, types, required fields, picklist values, and external ID flags.
   MUST call before write_record to discover valid field API names.
   For upsert: check `externalId: true` to find usable external ID fields.
3. **soql_query / search_records / write_record / process_approval** — Perform the operation.

## When to use which read tool
- **soql_query** — Precise field-level queries on a known object. Supports relationship \
queries, aggregates, GROUP BY, subqueries. Auto-paginates large results (use `max_records` \
to control the limit, default 10000). E.g., "SELECT Id, Name, (SELECT FirstName \
FROM Contacts) FROM Account WHERE Industry = 'Technology'"
- **search_records** — Full-text keyword search across multiple objects simultaneously. \
Use when you don't know which object contains the data. E.g., search for "Acme".

## write_record operations
- **create**: new record — requires object_name + field_values
- **update**: modify existing — requires object_name + record_id + field_values
- **upsert**: create-or-update by external ID — requires object_name + field_values + external_id_field. \
The external_id_field must be marked as an External ID in Salesforce (check describe_object). \
The Id field also works for upsert.
- **delete**: permanent removal — requires object_name + record_id. Confirm with user first.

## Important conventions
- Object/field names use Salesforce API names (PascalCase): Account, Contact, OpportunityLineItem.
- Field values use API field name as key: {"Name": "Acme", "Industry": "Technology"}.
- Record IDs are 18-character alphanumeric strings.
- Errors return {"success": false, "error": "..."} — follow the guidance in the message.

## Error handling
- Salesforce errors return {"success": false, "errorCode": "...", "message": "..."}.
- INSUFFICIENT_ACCESS = user lacks permission. Explain clearly what permission is missing.
- INVALID_FIELD = bad field name. Suggest running describe_object.
- When a tool returns an error, explain the reason to the user in plain language.
""",
    host="0.0.0.0",
    port=port,
)


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
    (e.g., filter="Account", filter="Order", filter="Case"). Without a filter,
    only the first 100 objects are returned alphabetically and you may miss
    the object you need.

    Args:
        filter: String to filter objects by name or label (case-insensitive). Strongly recommended.

    Returns:
        JSON array (max 100) of objects with name, label, queryable, createable, updateable, deletable flags.
    """
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

    return json.dumps(objects[:100], indent=2)


@mcp.tool()
async def describe_object(object_name: str) -> str:
    """Get detailed field metadata for a Salesforce object.

    Use this to discover field names, types, required fields, picklist values,
    relationships, and external ID fields before querying or writing records.
    Fields with externalId: true can be used for upsert operations.

    Args:
        object_name: The API name of the Salesforce object (e.g., Account, Contact, Opportunity).

    Returns:
        JSON with object name, label, fields (name, label, type, required, externalId, picklistValues, referenceTo),
        and child relationships.
    """
    try:
        result = await sf.describe_object(object_name)
    except httpx.HTTPStatusError as e:
        return _sf_error_response(e)
    return json.dumps(result, indent=2)


@mcp.tool()
async def soql_query(query: str, max_records: int = 10000) -> str:
    """Execute a raw SOQL query against Salesforce with automatic pagination.

    Takes a complete SOQL query string — the agent builds the query. Supports
    the full SOQL syntax including relationship queries, aggregates, GROUP BY,
    HAVING, date functions, and subqueries. Read-only (SOQL cannot mutate data).

    Results are automatically paginated — large result sets that exceed
    Salesforce's page size (~2000) are fetched in full up to max_records.

    Examples:
        - Relationship query: "SELECT Id, Name, (SELECT FirstName, LastName FROM Contacts) FROM Account LIMIT 5"
        - Aggregate: "SELECT Industry, COUNT(Id) cnt FROM Account GROUP BY Industry"
        - Parent lookup: "SELECT Id, Name, Account.Name FROM Contact LIMIT 5"

    Args:
        query: Complete SOQL query string. Use describe_object to discover field names first.
        max_records: Maximum records to return (default 10000, cap 50000). Prevents runaway pagination.

    Returns:
        JSON with totalSize, records array, and done flag. done is false if results were truncated by max_records.
    """
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

    return json.dumps(
        {
            "totalSize": total_size,
            "records": records[:max_records],
            "done": result.get("done", True) and len(records) <= max_records,
        },
        indent=2,
    )


@mcp.tool()
async def search_records(
    search_term: str,
    objects: str | None = None,
    limit: int = 20,
) -> str:
    """Search across multiple Salesforce objects using full-text search (SOSL).

    SOSL searches across multiple objects simultaneously using full-text indexing.
    Use this when you don't know which object contains the data, or for keyword
    searches. For precise field-level filtering on a known object, use soql_query instead.

    Args:
        search_term: Plain text to search for (e.g., "Acme", "john.doe@example.com").
            Special characters are auto-escaped.
        objects: Optional SOSL RETURNING clause specifying which objects and fields to return.
            E.g., "Account(Name, Industry), Contact(FirstName, LastName, Email)".
            If omitted, searches all searchable objects with default fields.
        limit: Maximum total records to return (default 20, max 200).

    Returns:
        JSON with searchRecords array containing matched records across objects.
    """
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

    return json.dumps({"searchRecords": records}, indent=2)


@mcp.tool()
async def write_record(
    object_name: str,
    operation: str,
    field_values: dict | None = None,
    record_id: str | None = None,
    external_id_field: str | None = None,
) -> str:
    """Create, update, upsert, or delete a Salesforce record.

    IMPORTANT: Call describe_object first to discover valid field API names and
    required fields. Field names are validated before sending.

    Operations:
        - create: New record. Requires field_values.
        - update: Partial update. Requires record_id + field_values.
        - upsert: Create-or-update by external ID. Requires field_values + external_id_field.
            The external ID value must be included in field_values.
        - delete: Permanent removal. Requires record_id. Confirm with the user first.

    Args:
        object_name: The API name of the Salesforce object (e.g., Account, Contact).
        operation: One of "create", "update", "upsert", "delete".
        field_values: Field API names to values. Required for create/update/upsert, ignored for delete.
            E.g., {"Name": "Acme Corp", "Industry": "Technology"}.
        record_id: 18-character Salesforce record ID. Required for update/delete.
        external_id_field: External ID field API name for upsert (e.g., "External_Id__c").
            The field value must also be in field_values.

    Returns:
        JSON with success flag and details (e.g., id for create, created flag for upsert).
    """
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

    return json.dumps(result, indent=2)


@mcp.tool()
async def process_approval(
    action: str,
    record_id: str,
    comments: str | None = None,
) -> str:
    """Submit, approve, or reject a Salesforce approval request.

    To find pending approvals, use soql_query:
        "SELECT Id, ProcessInstance.TargetObjectId, ProcessInstance.TargetObject.Name,
         Actor.Name, CreatedDate FROM ProcessInstanceWorkitem WHERE Actor.Id = '...'"

    Confirm with the user before approving or rejecting.

    Args:
        action: One of "Submit", "Approve", "Reject".
        record_id: For Submit — the record ID to submit for approval.
            For Approve/Reject — the ProcessInstanceWorkitem ID.
        comments: Optional comments for the approval action.

    Returns:
        JSON with success flag and approval result details.
    """
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
    if len(items) == 1:
        return json.dumps(items[0], indent=2)
    return json.dumps(result, indent=2)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Extract Authorization: Bearer token and set it as the per-request context var.

    When a bearer token is present (e.g., from APIM), the SalesforceClient uses
    it directly without retry/refresh. When absent (local dev), the existing
    self-managed token flow kicks in unchanged.
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
