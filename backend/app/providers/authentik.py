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

    def export_identities(self) -> dict[str, list[dict]]:
        with self._client() as c:
            raw = self._paged(c, "/api/v3/core/users/")
            users, memberships = [], []
            for u in raw:
                uid = u.get("pk")
                users.append({"id": uid, "username": u.get("username"),
                              "name": u.get("name"), "email": u.get("email"),
                              "is_active": u.get("is_active"), "type": u.get("type"),
                              "attributes": u.get("attributes", {}),
                              "path": u.get("path")})
                for gid in (u.get("groups") or []):
                    memberships.append({"group_id": gid, "user_id": uid})
            # Authentik app access is governed by policy bindings (captured in config),
            # which already record group-vs-user provenance — so those buckets stay empty here.
            groups = self._paged(c, "/api/v3/core/groups/")
            group_ref = [{"id": g.get("pk"), "name": g.get("name")} for g in groups]
            return {"users": users, "group_memberships": memberships,
                    "app_group_assignments": [], "app_user_assignments_direct": [],
                    "group_ref": group_ref, "app_ref": []}

    def _write(self, c, method, path, **kw):
        r = c.request(method, path, **kw)
        if r.status_code >= 400 and r.status_code not in (409,):
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r

    def apply_identities(self, snap: dict, only_keys=None) -> dict:
        """Additive restore: create missing users (by username), re-add group
        memberships. App access is governed by config policy bindings (restore config
        for that). Resolved by natural key so recreated-object ids don't break edges."""
        rep = {"users": {"created": 0, "existing": 0, "skipped": 0, "failed": []},
               "group_memberships": {"added": 0, "skipped": 0, "failed": []},
               "app_group_assignments": {"added": 0, "skipped": 0, "failed": []},
               "app_user_assignments_direct": {"added": 0, "skipped": 0, "failed": []}}
        with self._client() as c:
            live = self.export_identities()
            live_user = {u.get("username"): u.get("id") for u in live.get("users", []) if u.get("username")}
            live_group = {g["name"]: g["id"] for g in live.get("group_ref", []) if g.get("name")}
            live_group_ids = {g["id"] for g in live.get("group_ref", [])}
            snap_user_name = {u.get("id"): u.get("username") for u in snap.get("users", [])}
            snap_group_name = {g["id"]: g["name"] for g in snap.get("group_ref", [])}

            def r_group(gid):
                name = snap_group_name.get(gid)
                if name and name in live_group:
                    return live_group[name]
                return gid if gid in live_group_ids else None

            for u in snap.get("users", []):
                uname = u.get("username")
                if not uname:
                    rep["users"]["failed"].append({"user": u.get("id"), "error": "no username"})
                    continue
                if uname in live_user:
                    rep["users"]["existing"] += 1
                    continue
                if only_keys is not None and uname not in only_keys:
                    rep["users"]["skipped"] += 1
                    continue
                try:
                    body = {"username": uname, "name": u.get("name") or uname,
                            "email": u.get("email") or "", "is_active": bool(u.get("is_active", True)),
                            "type": u.get("type") or "internal", "path": u.get("path") or "users",
                            "attributes": u.get("attributes") or {}}
                    r = self._write(c, "POST", "/api/v3/core/users/", json=body)
                    live_user[uname] = r.json().get("pk")
                    rep["users"]["created"] += 1
                except Exception as e:
                    rep["users"]["failed"].append({"user": uname, "error": str(e)[:200]})

            live_mem = {(e["group_id"], e["user_id"]) for e in live.get("group_memberships", [])}
            for e in snap.get("group_memberships", []):
                lg = r_group(e["group_id"])
                lu = live_user.get(snap_user_name.get(e["user_id"]))
                if not lg or not lu:
                    rep["group_memberships"]["skipped"] += 1
                    continue
                if (lg, lu) in live_mem:
                    rep["group_memberships"]["skipped"] += 1
                    continue
                try:
                    self._write(c, "POST", f"/api/v3/core/groups/{lg}/add_user/", json={"pk": lu})
                    rep["group_memberships"]["added"] += 1
                except Exception as ex:
                    rep["group_memberships"]["failed"].append({"edge": f"{lg}/{lu}", "error": str(ex)[:150]})
        return rep
