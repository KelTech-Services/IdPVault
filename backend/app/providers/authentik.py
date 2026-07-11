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
    "flow_stage_bindings": "/api/v3/flows/bindings/",
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

    CHANGE_ACTIONS = {"model_created", "model_updated", "model_deleted"}

    def count_changes_since(self, iso_ts: str) -> int | None:
        """Filter params on the events API are unreliable across versions —
        page newest-first and count client-side, capped at 500."""
        count, page = 0, 1
        with self._client() as c:
            while page <= 5:
                r = c.get("/api/v3/events/events/",
                          params={"ordering": "-created", "page_size": 100, "page": page})
                if r.status_code != 200:
                    return None if page == 1 else count
                results = r.json().get("results", [])
                if not results:
                    return count
                for ev in results:
                    created = str(ev.get("created", ""))[:19].replace(" ", "T")
                    if created < iso_ts[:19]:
                        return count
                    if ev.get("action") in self.CHANGE_ACTIONS:
                        count += 1
                if not r.json().get("pagination", {}).get("next"):
                    return count
                page += 1
        return count

    # ---- restore support ----
    READONLY_FIELDS = {"pk", "component", "verbose_name", "verbose_name_plural",
                       "meta_model_name", "managed", "object_uid", "assigned_application_slug",
                       "assigned_application_name", "assigned_backchannel_application_slug",
                       "assigned_backchannel_application_name", "outpost_set", "url_download_metadata"}

    _EXACT_PATHS = {
        "authentik_core.application": "core/applications/",
        "authentik_core.group": "core/groups/",
        "authentik_flows.flow": "flows/instances/",
        "authentik_flows.flowstagebinding": "flows/bindings/",
        "authentik_policies.policybinding": "policies/bindings/",
        "authentik_brands.brand": "core/brands/",
        "authentik_crypto.certificatekeypair": "crypto/certificatekeypairs/",
        "authentik_outposts.outpost": "outposts/instances/",
        "authentik_providers_oauth2.scopemapping": "propertymappings/provider/scope/",
    }

    def _write_path(self, obj: dict) -> str | None:
        model = obj.get("meta_model_name", "")
        if model in self._EXACT_PATHS:
            return self._EXACT_PATHS[model]
        app, _, cls = model.partition(".")
        if cls.endswith("propertymapping") or cls.endswith("scopemapping"):
            if app.startswith("authentik_providers_"):
                return f"propertymappings/provider/{app.removeprefix('authentik_providers_')}/"
            if app.startswith("authentik_sources_"):
                return f"propertymappings/source/{app.removeprefix('authentik_sources_')}/"
            return None
        for prefix, seg in (("authentik_policies_", "policies/"), ("authentik_stages_", "stages/"),
                            ("authentik_providers_", "providers/"), ("authentik_sources_", "sources/")):
            if app.startswith(prefix):
                return seg + app.removeprefix(prefix).replace("_", "/") + "/"
        return None

    def push_object(self, resource_type: str, obj: dict) -> tuple[str, str]:
        """Create-or-update one object from a snapshot. Returns (action, live_pk)."""
        if obj.get("managed"):
            return ("skipped_managed", str(obj.get("pk", "")))
        path = self._write_path(obj)
        if not path:
            raise RuntimeError(f"no write path known for {obj.get('meta_model_name')!r}")
        payload = {k: v for k, v in obj.items() if k not in self.READONLY_FIELDS}
        pk = obj.get("pk") or obj.get("brand_uuid")
        with self._client() as c:
            if pk is not None:
                live = c.get(f"/api/v3/{path}{pk}/")
                if live.status_code == 200:
                    r = c.put(f"/api/v3/{path}{pk}/", json=payload)
                    if r.status_code >= 400:
                        raise RuntimeError(f"PUT {path}{pk}/ -> {r.status_code}: {r.text[:280]}")
                    return ("updated", str(pk))
            r = c.post(f"/api/v3/{path}", json=payload)
            if r.status_code >= 400:
                raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:280]}")
            return ("created", str(r.json().get("pk", "")))
