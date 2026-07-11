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
    "user_types": "/api/v1/meta/types/user",
    "profile_mappings": "/api/v1/mappings",
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

    APP_SCHEMA_CAP = 200  # safety cap for large orgs (per-app schema = 1 call each)

    def export(self) -> dict[str, list[dict]]:
        with self._client() as c:
            out = {rtype: self._paged(c, path) for rtype, path in RESOURCES.items()}

            # per-user-type schemas (default already captured; add non-default types)
            type_schemas = []
            for ut in out.get("user_types", []):
                href = (ut.get("_links", {}).get("schema", {}) or {}).get("href", "")
                if not href:
                    continue
                path = href.replace(self.base_url, "")
                r = c.get(path)
                if r.status_code == 200:
                    doc = r.json()
                    doc["_user_type"] = ut.get("id")
                    type_schemas.append(doc)
            out["user_type_schemas"] = type_schemas

            # per-app user schemas (app-level custom attributes), capped
            app_schemas = []
            for a in out.get("apps", [])[:self.APP_SCHEMA_CAP]:
                aid = a.get("id")
                if not aid:
                    continue
                r = c.get(f"/api/v1/meta/schemas/apps/{aid}/default")
                if r.status_code == 200:
                    doc = r.json()
                    doc["_app_id"] = aid
                    app_schemas.append(doc)
            out["app_user_schemas"] = app_schemas
            return out

    def count_changes_since(self, iso_ts: str) -> int | None:
        with self._client() as c:
            r = c.get("/api/v1/logs", params={"since": iso_ts, "limit": 1000,
                                              "filter": 'eventType sw "system.' + '" or eventType sw "policy." or eventType sw "application." or eventType sw "group."'})
            if r.status_code != 200:
                return None
            return len(r.json())
