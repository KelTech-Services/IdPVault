"""Authentik adapter. Auth: API token (Bearer). Paginated via ?page= / pagination.next."""
import httpx

from app.providers.base import ProviderAdapter

RESOURCES = {
    "applications": "/api/v3/core/applications/",
    "providers": "/api/v3/providers/all/",
    "flows": "/api/v3/flows/instances/",
    "stages": "/api/v3/stages/all/",
    "policies": "/api/v3/policies/all/",
    "policy_bindings": "/api/v3/policies/bindings/",
    "property_mappings": "/api/v3/propertymappings/all/",
    "groups": "/api/v3/core/groups/",
    "brands": "/api/v3/core/brands/",
    "outposts": "/api/v3/outposts/instances/",
    "certificates": "/api/v3/crypto/certificatekeypairs/",
    "blueprints": "/api/v3/managed/blueprints/",
}


class AuthentikAdapter(ProviderAdapter):
    name = "authentik"

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.credentials}"},
            timeout=30,
        )

    def validate_credentials(self) -> bool:
        with self._client() as c:
            return c.get("/api/v3/core/users/me/").status_code == 200

    def _paged(self, c: httpx.Client, path: str) -> list[dict]:
        out, page = [], 1
        while True:
            r = c.get(path, params={"page": page, "page_size": 100})
            r.raise_for_status()
            body = r.json()
            out.extend(body.get("results", []))
            if not body.get("pagination", {}).get("next"):
                return out
            page += 1

    def export(self) -> dict[str, list[dict]]:
        with self._client() as c:
            return {rtype: self._paged(c, path) for rtype, path in RESOURCES.items()}
