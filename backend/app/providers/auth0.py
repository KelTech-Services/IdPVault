"""Auth0 adapter. Auth: Management API token (Bearer). Paginated via page/per_page."""
import httpx

from app.providers.base import ProviderAdapter

RESOURCES = {
    "clients": "/api/v2/clients",
    "connections": "/api/v2/connections",
    "resource_servers": "/api/v2/resource-servers",
    "roles": "/api/v2/roles",
    "actions": "/api/v2/actions/actions",
    "rules": "/api/v2/rules",
    "email_templates": None,  # fetched individually; TODO
    "tenant_settings": "/api/v2/tenants/settings",
    "custom_domains": "/api/v2/custom-domains",
    "branding": "/api/v2/branding",
}


class Auth0Adapter(ProviderAdapter):
    name = "auth0"

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.credentials}"},
            timeout=30,
        )

    def validate_credentials(self) -> bool:
        with self._client() as c:
            return c.get("/api/v2/tenants/settings").status_code == 200

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
