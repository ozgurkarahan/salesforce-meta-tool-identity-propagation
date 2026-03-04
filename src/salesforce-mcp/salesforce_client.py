"""Salesforce REST API client — bearer passthrough mode (OBO via APIM).

All authentication is handled by APIM's OBO token exchange policy. The client
receives a per-request Salesforce access token via the BearerTokenMiddleware
context var and uses it directly — no token management, refresh, or secrets.
"""

import contextvars
import os
import time

import httpx

# Per-request bearer token set by BearerTokenMiddleware.
# The APIM gateway exchanges the user's Azure AD token for a Salesforce token
# and forwards it in the Authorization header. This context var carries it
# through to every _request() call.
_request_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_request_token", default=None
)


class SalesforceClient:
    """Async Salesforce REST API client (bearer passthrough only).

    Expects a per-request token from APIM via the _request_token context var.
    Falls back to SF_ACCESS_TOKEN env var for local testing only.
    """

    def __init__(self):
        self.api_version = os.environ.get("SF_API_VERSION", "v62.0")
        self.instance_url: str | None = os.environ.get("SF_INSTANCE_URL")

        # Optional: pre-supplied token for local testing (not used in production)
        self._fallback_token: str | None = os.environ.get("SF_ACCESS_TOKEN")

        # Describe cache: object_name -> (timestamp, data)
        self._describe_cache: dict[str, tuple[float, dict]] = {}
        self._global_describe_cache: tuple[float, list] | None = None
        self._cache_ttl = 900  # 15 minutes

        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def _base_url(self) -> str:
        return f"{self.instance_url}/services/data/{self.api_version}"

    async def _request(
        self, method: str, path: str, *, _absolute: bool = False, **kwargs
    ) -> httpx.Response:
        """Make an authenticated request using the per-request bearer token.

        Args:
            _absolute: When True, use instance_url + path instead of base_url + path.
                       Used for pagination URLs that already include the API version.
        """
        token = _request_token.get() or self._fallback_token
        if not token:
            raise RuntimeError(
                "No bearer token available. In production, APIM provides the token "
                "via the Authorization header. For local testing, set SF_ACCESS_TOKEN."
            )

        url = f"{self.instance_url}{path}" if _absolute else f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = await self._client.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp

    async def describe_global(self) -> list[dict]:
        """List all sObjects with permission flags. Cached for 15 min."""
        now = time.time()
        if self._global_describe_cache:
            ts, data = self._global_describe_cache
            if now - ts < self._cache_ttl:
                return data

        resp = await self._request("GET", "/sobjects/")
        sobjects = resp.json()["sobjects"]
        result = [
            {
                "name": obj["name"],
                "label": obj["label"],
                "queryable": obj["queryable"],
                "createable": obj["createable"],
                "updateable": obj["updateable"],
                "deletable": obj["deletable"],
            }
            for obj in sobjects
        ]
        self._global_describe_cache = (now, result)
        return result

    async def describe_object(self, object_name: str, slim: bool = False) -> dict:
        """Get field metadata for an sObject. Cached for 15 min.

        Args:
            slim: When True, return only {name, fields: [{name, type, required}]}.
                  Full result is always cached — slim just filters the response.
        """
        now = time.time()
        if object_name in self._describe_cache:
            ts, data = self._describe_cache[object_name]
            if now - ts < self._cache_ttl:
                return self._slim_describe(data) if slim else data

        resp = await self._request("GET", f"/sobjects/{object_name}/describe/")
        raw = resp.json()

        fields = [
            {
                "name": f["name"],
                "label": f["label"],
                "type": f["type"],
                "required": not f["nillable"] and not f["defaultedOnCreate"],
                "externalId": f.get("externalId", False),
                "picklistValues": [
                    {"value": pv["value"], "label": pv["label"]}
                    for pv in f.get("picklistValues", [])
                    if pv.get("active")
                ] or None,
                "referenceTo": f.get("referenceTo") or None,
            }
            for f in raw["fields"]
        ]

        child_relationships = [
            {
                "childSObject": cr["childSObject"],
                "relationshipName": cr["relationshipName"],
                "field": cr["field"],
            }
            for cr in raw.get("childRelationships", [])
            if cr.get("relationshipName")
        ]

        result = {
            "name": raw["name"],
            "label": raw["label"],
            "fields": fields,
            "childRelationships": child_relationships,
        }
        self._describe_cache[object_name] = (now, result)
        return self._slim_describe(result) if slim else result

    @staticmethod
    def _slim_describe(full: dict) -> dict:
        """Strip a cached describe result down to names, types, and required flags."""
        return {
            "name": full["name"],
            "fields": [
                {"name": f["name"], "type": f["type"], "required": f["required"]}
                for f in full["fields"]
            ],
        }

    async def query(self, soql: str) -> dict:
        """Execute a SOQL query. Returns {totalSize, records, done, nextRecordsUrl}."""
        resp = await self._request("GET", "/query/", params={"q": soql})
        return resp.json()

    async def query_more(self, next_url: str) -> dict:
        """Fetch the next page of a SOQL query using nextRecordsUrl.

        Args:
            next_url: The nextRecordsUrl path from a previous query result
                (e.g., /services/data/v62.0/query/01gxx...-2000).
        """
        resp = await self._request("GET", next_url, _absolute=True)
        return resp.json()

    async def create_record(self, object_name: str, field_values: dict) -> dict:
        """Create a new sObject record."""
        resp = await self._request(
            "POST", f"/sobjects/{object_name}/", json=field_values
        )
        return resp.json()

    async def update_record(
        self, object_name: str, record_id: str, field_values: dict
    ) -> dict:
        """Update an existing sObject record."""
        resp = await self._request(
            "PATCH", f"/sobjects/{object_name}/{record_id}", json=field_values
        )
        # PATCH returns 204 No Content on success
        if resp.status_code == 204:
            return {"success": True}
        return resp.json()

    async def delete_record(self, object_name: str, record_id: str) -> dict:
        """Delete an sObject record."""
        resp = await self._request(
            "DELETE", f"/sobjects/{object_name}/{record_id}"
        )
        # DELETE returns 204 No Content on success
        if resp.status_code == 204:
            return {"success": True}
        return resp.json()

    async def search(self, sosl: str) -> dict:
        """Execute a SOSL search query. Returns {"searchRecords": [...]}."""
        resp = await self._request(
            "GET", "/search/", params={"q": sosl}
        )
        return resp.json()

    async def upsert_record(
        self,
        object_name: str,
        external_id_field: str,
        external_id_value: str,
        field_values: dict,
    ) -> dict:
        """Upsert (create-or-update) a record by external ID field."""
        resp = await self._request(
            "PATCH",
            f"/sobjects/{object_name}/{external_id_field}/{external_id_value}",
            json=field_values,
        )
        # 201 = created, 204 = updated
        if resp.status_code == 201:
            return {"success": True, "created": True, **resp.json()}
        if resp.status_code == 204:
            return {"success": True, "created": False}
        return resp.json()

    async def process_approval(self, requests: list[dict]) -> dict:
        """Submit, approve, or reject approval requests."""
        resp = await self._request(
            "POST", "/process/approvals/", json={"requests": requests}
        )
        return resp.json()

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
