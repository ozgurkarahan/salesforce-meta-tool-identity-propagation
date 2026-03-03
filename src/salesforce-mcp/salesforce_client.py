"""Salesforce REST API client with auth, describe, query, and CRUD operations."""

import contextvars
import os
import time
import urllib.parse

import httpx

# Per-request bearer token for passthrough mode (set by middleware).
# When set, _request() and query_more() use this token directly and do NOT
# retry/refresh on 401 — the upstream caller owns the token lifecycle.
_request_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_request_token", default=None
)


class SalesforceClient:
    """Async Salesforce REST API client.

    Supports two auth modes:
    - Pre-supplied token: set SF_ACCESS_TOKEN + SF_INSTANCE_URL env vars
    - Authorization code flow: interactive browser login (default fallback)
    """

    REDIRECT_URI = "http://localhost:8443/callback"

    def __init__(self):
        self.client_id = os.environ.get("SF_CLIENT_ID", "")
        self.client_secret = os.environ.get("SF_CLIENT_SECRET", "")
        self.login_url = os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com")
        self.api_version = os.environ.get("SF_API_VERSION", "v62.0")

        # Pre-supplied token (from auth code flow or external source)
        self.access_token: str | None = os.environ.get("SF_ACCESS_TOKEN")
        self.instance_url: str | None = os.environ.get("SF_INSTANCE_URL")
        self.refresh_token: str | None = None

        # Describe cache: object_name -> (timestamp, data)
        self._describe_cache: dict[str, tuple[float, dict]] = {}
        self._global_describe_cache: tuple[float, list] | None = None
        self._cache_ttl = 900  # 15 minutes

        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def _base_url(self) -> str:
        return f"{self.instance_url}/services/data/{self.api_version}"

    async def authenticate(self) -> None:
        """Authenticate via interactive authorization code flow (opens browser)."""
        if self.access_token and self.instance_url:
            return  # Already authenticated

        if not self.client_id:
            raise RuntimeError("SF_CLIENT_ID is required for authentication")

        auth_code = self._get_auth_code_via_browser()
        resp = await self._client.post(
            f"{self.login_url}/services/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": auth_code,
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["access_token"]
        self.instance_url = data["instance_url"]
        self.refresh_token = data.get("refresh_token")

    def _get_auth_code_via_browser(self) -> str:
        """Open browser for OAuth login, capture authorization code via local callback."""
        import http.server
        import threading
        import webbrowser

        result = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "code" in params:
                    result["code"] = params["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h2>Success! You can close this tab.</h2>")
                else:
                    result["error"] = params.get("error", ["unknown"])[0]
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(f"<h2>Error: {result['error']}</h2>".encode())

            def log_message(self, format, *args):
                pass

        server = http.server.HTTPServer(("localhost", 8443), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        authorize_url = (
            f"{self.login_url}/services/oauth2/authorize"
            f"?response_type=code"
            f"&client_id={urllib.parse.quote(self.client_id)}"
            f"&redirect_uri={urllib.parse.quote(self.REDIRECT_URI)}"
            f"&scope=api+refresh_token"
        )
        print("Opening browser for Salesforce login...")
        webbrowser.open(authorize_url)

        thread.join(timeout=120)
        server.server_close()

        if "error" in result:
            raise RuntimeError(f"Authorization failed: {result['error']}")
        if "code" not in result:
            raise RuntimeError("Timed out waiting for authorization callback")

        return result["code"]

    async def _refresh_access_token(self) -> bool:
        """Try to refresh the access token using stored refresh token. Returns True on success."""
        if not self.refresh_token or not self.client_id:
            return False
        try:
            resp = await self._client.post(
                f"{self.login_url}/services/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self.access_token = data["access_token"]
            if "refresh_token" in data:
                self.refresh_token = data["refresh_token"]
            return True
        except httpx.HTTPError:
            return False

    async def _request(
        self, method: str, path: str, *, _absolute: bool = False, **kwargs
    ) -> httpx.Response:
        """Make an authenticated request, auto-refreshing on 401.

        Args:
            _absolute: When True, use instance_url + path instead of base_url + path.
                       Used for pagination URLs that already include the API version.
        """
        # Passthrough mode: use per-request token from middleware (no retry/refresh)
        passthrough_token = _request_token.get()
        if passthrough_token:
            url = f"{self.instance_url}{path}" if _absolute else f"{self._base_url}{path}"
            headers = {"Authorization": f"Bearer {passthrough_token}"}
            resp = await self._client.request(method, url, headers=headers, **kwargs)
            resp.raise_for_status()
            return resp

        if not self.access_token:
            await self.authenticate()

        url = f"{self.instance_url}{path}" if _absolute else f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self.access_token}"}

        resp = await self._client.request(method, url, headers=headers, **kwargs)

        if resp.status_code == 401:
            self.access_token = None
            if not await self._refresh_access_token():
                await self.authenticate()
            headers = {"Authorization": f"Bearer {self.access_token}"}
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

    async def describe_object(self, object_name: str) -> dict:
        """Get full field metadata for an sObject. Cached for 15 min."""
        now = time.time()
        if object_name in self._describe_cache:
            ts, data = self._describe_cache[object_name]
            if now - ts < self._cache_ttl:
                return data

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
        return result

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
