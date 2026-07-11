"""Okta adapter. Auth: SSWS API token. Paginated via Link: rel="next" headers."""
import httpx

from app.providers.base import ProviderAdapter

RESOURCES = {
    "apps": "/api/v1/apps",
    "groups": "/api/v1/groups",
    "policies_signon": "/api/v1/policies?type=OKTA_SIGN_ON",
    "policies_password": "/api/v1/policies?type=PASSWORD",
    "policies_mfa": "/api/v1/policies?type=MFA_ENROLL",
    "policies_access": "/api/v1/policies?type=ACCESS_POLICY",
    "authorization_servers": "/api/v1/authorizationServers",
    "idps": "/api/v1/idps",
    "network_zones": "/api/v1/zones",
    "user_schemas": "/api/v1/meta/schemas/user/default",
    "event_hooks": "/api/v1/eventHooks",
    "inline_hooks": "/api/v1/inlineHooks",
}


class OktaAdapter(ProviderAdapter):
    name = "okta"

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"SSWS {self.credentials}"},
            timeout=30,
        )

    def validate_credentials(self) -> bool:
        with self._client() as c:
            return c.get("/api/v1/org").status_code == 200

    def _paged(self, c: httpx.Client, path: str) -> list[dict]:
        out, url = [], path
        while url:
            r = c.get(url)
            r.raise_for_status()
            body = r.json()
            out.extend(body if isinstance(body, list) else [body])
            url = r.links.get("next", {}).get("url")
        return out

    def export(self) -> dict[str, list[dict]]:
        with self._client() as c:
            return {rtype: self._paged(c, path) for rtype, path in RESOURCES.items()}
