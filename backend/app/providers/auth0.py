"""Auth0 adapter. Auth: OAuth2 client-credentials — the stored credential is
"client_id:client_secret"; the adapter mints a short-lived Management API token
per run (cached for its lifetime) and calls the Management API with it.
Paginated via page/per_page.
"""
import time

import httpx

from app.providers.base import ProviderAdapter

RESOURCES = {
    "clients": "/api/v2/clients",
    "connections": "/api/v2/connections",
    "resource_servers": "/api/v2/resource-servers",
    "roles": "/api/v2/roles",
    "actions": "/api/v2/actions/actions",
    "rules": "/api/v2/rules",
    "tenant_settings": "/api/v2/tenants/settings",
    "custom_domains": "/api/v2/custom-domains",
    "branding": "/api/v2/branding",
}


class Auth0Adapter(ProviderAdapter):
    name = "auth0"

    def __init__(self, base_url: str, credentials: str):
        super().__init__(base_url, credentials)
        self._tok = None
        self._tok_exp = 0.0

    def _creds(self) -> tuple[str, str]:
        cid, _, secret = self.credentials.partition(":")
        return cid, secret

    def _token(self) -> str:
        if self._tok and time.time() < self._tok_exp - 60:
            return self._tok
        cid, secret = self._creds()
        r = httpx.post(f"{self.base_url}/oauth/token", timeout=30, json={
            "client_id": cid, "client_secret": secret,
            "audience": f"{self.base_url}/api/v2/",
            "grant_type": "client_credentials"})
        r.raise_for_status()
        body = r.json()
        self._tok = body["access_token"]
        self._tok_exp = time.time() + float(body.get("expires_in", 86400))
        return self._tok

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=30,
        )

    def validate_credentials(self) -> bool:
        try:
            with self._client() as c:
                return c.get("/api/v2/tenants/settings").status_code == 200
        except Exception:
            return False

    def _paged(self, c: httpx.Client, path: str) -> list[dict]:
        out, page = [], 0
        while True:
            r = c.get(path, params={"page": page, "per_page": 100})
            r.raise_for_status()
            body = r.json()
            items = body if isinstance(body, list) else body.get("actions", [body])
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1

    def export(self) -> dict[str, list[dict]]:
        with self._client() as c:
            return {rtype: self._paged(c, path)
                    for rtype, path in RESOURCES.items() if path}
