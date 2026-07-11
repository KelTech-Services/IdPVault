"""Okta adapter. Auth: SSWS API token. Paginated via Link: rel="next" headers."""
import httpx

from app.core.ratelimit import AdaptiveRateLimiter
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

    def __init__(self, base_url: str, credentials: str):
        super().__init__(base_url, credentials)
        self._rl = AdaptiveRateLimiter(reserve_pct=self._reserve_pct())

    @staticmethod
    def _reserve_pct() -> float:
        try:
            from app.models.db import SessionLocal, Setting
            with SessionLocal() as db:
                row = db.get(Setting, "general")
                v = row.value.get("okta_rate_reserve_pct") if row else None
                return float(v) / 100.0 if v is not None else 0.2
        except Exception:
            return 0.2

    def _get(self, c, path, **kw):
        return self._rl.request(lambda: c.get(path, **kw))

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"SSWS {self.credentials}"},
            timeout=30,
        )

    def validate_credentials(self) -> bool:
        with self._client() as c:
            return self._get(c, "/api/v1/org").status_code == 200

    def _paged(self, c: httpx.Client, path: str, params: dict | None = None) -> list[dict]:
        out, url, first = [], path, True
        while url:
            r = self._get(c, url, params=params if first else None)
            first = False
            r.raise_for_status()
            body = r.json()
            out.extend(body if isinstance(body, list) else [body])
            url = r.links.get("next", {}).get("url")
        return out

    @staticmethod
    def _slim_user(u: dict) -> dict:
        return {"id": u.get("id"), "status": u.get("status"),
                "type": u.get("type"), "profile": u.get("profile", {}),
                "created": u.get("created")}

    def export_identities(self) -> dict[str, list[dict]]:
        with self._client() as c:
            # all users incl. deprovisioned (search=status pr returns every status)
            users = [self._slim_user(u) for u in
                     self._paged(c, "/api/v1/users", params={"search": "status pr", "limit": 200})]
            groups = self._paged(c, "/api/v1/groups", params={"limit": 200})
            apps = self._paged(c, "/api/v1/apps", params={"limit": 200})

            memberships = []
            for g in groups:
                gid = g.get("id")
                for m in self._paged(c, f"/api/v1/groups/{gid}/users", params={"limit": 200}):
                    memberships.append({"group_id": gid, "user_id": m.get("id")})

            app_group, app_user_direct = [], []
            for a in apps:
                aid = a.get("id")
                for ag in self._paged(c, f"/api/v1/apps/{aid}/groups", params={"limit": 200}):
                    app_group.append({"app_id": aid, "group_id": ag.get("id")})
                for au in self._paged(c, f"/api/v1/apps/{aid}/users", params={"limit": 200}):
                    if au.get("scope") == "USER":  # DIRECT only; GROUP-inherited excluded
                        app_user_direct.append({"app_id": aid, "user_id": au.get("id")})

            return {"users": users, "group_memberships": memberships,
                    "app_group_assignments": app_group,
                    "app_user_assignments_direct": app_user_direct}


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
                r = self._get(c, path)
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
                r = self._get(c, f"/api/v1/meta/schemas/apps/{aid}/default")
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
