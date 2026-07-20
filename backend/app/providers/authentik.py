"""Authentik adapter. Auth: API token (Bearer). Paginated via ?page= / pagination.next."""
import httpx

from app.providers.base import ProviderAdapter

RESOURCES = {
    # superuser_full_list: WITHOUT it Authentik filters this list through the
    # access-policy engine, so every policy-protected app silently vanishes
    # from exports (requires the token's user to be a superuser).
    "applications": "/api/v3/core/applications/?superuser_full_list=true",
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
    supports_identity = True
    restore_order = ["certificates", "property_mappings", "flows", "stages", "policies",
                     "groups", "providers", "applications", "flow_stage_bindings",
                     "policy_bindings", "outposts", "brands"]
    never_restore = {"blueprints", "user_schemas"}
    # Natural keys used to remap stale snapshot pks -> current live pks (an object
    # deleted + recreated gets a new pk; anything referencing the old pk must follow).
    _NK = {"applications": "slug", "flows": "slug", "groups": "name",
           "policies": "name", "stages": "name", "providers": "name",
           "property_mappings": "name", "certificates": "name", "brands": "domain"}
    # Reference fields that may carry pks of other objects (bindings, app->provider).
    _REF_FIELDS = ("target", "policy", "stage", "flow", "provider", "providers",
                   "group", "user", "authorization_flow", "authentication_flow",
                   "invalidation_flow")

    def __init__(self, base_url: str, credentials: str):
        super().__init__(base_url, credentials)
        self._pk_remap: dict = {}
        self._live_pks: set = set()
        self._snap_pks: set = set()

    def natural_key(self, resource_type: str, obj: dict) -> str:
        field = self._NK.get(resource_type)
        if field and obj.get(field) is not None:
            return str(obj[field])
        # Bindings have no name — identity is WHAT they connect. Composite key over
        # their references (with old->live pk remapping applied) so a binding whose
        # target was deleted+recreated still matches its live counterpart instead
        # of being re-created as a duplicate.
        if resource_type in ("policy_bindings", "flow_stage_bindings"):
            rm = lambda v: self._pk_remap.get(str(v), v) if v is not None else None  # noqa: E731
            if resource_type == "policy_bindings":
                parts = (rm(obj.get("policy")), rm(obj.get("group")),
                         rm(obj.get("user")), rm(obj.get("target")), obj.get("order"))
            else:
                parts = (rm(obj.get("target")), rm(obj.get("stage")), obj.get("order"))
            return "|".join(str(p) for p in parts)
        return super().natural_key(resource_type, obj)

    def begin_restore(self, snap_export: dict, live_export: dict) -> None:
        """Prebuild old-pk -> live-pk remaps by natural key, so references to
        objects that were deleted and recreated (new pk) resolve — including
        recreations from PREVIOUS restore runs."""
        self._pk_remap = {}
        self._live_pks = {str(o["pk"]) for objs in live_export.values()
                          for o in objs if isinstance(o, dict) and o.get("pk")}
        self._snap_pks = {str(o["pk"]) for objs in snap_export.values()
                          for o in objs if isinstance(o, dict) and o.get("pk")}
        for rtype, field in self._NK.items():
            live_by_key = {o.get(field): o.get("pk")
                           for o in live_export.get(rtype, []) if o.get(field) is not None}
            for o in snap_export.get(rtype, []):
                key, old = o.get(field), o.get("pk")
                new = live_by_key.get(key)
                if key is not None and old is not None and new is not None and new != old:
                    self._pk_remap[str(old)] = new

    def unrestorable_reason(self, resource_type: str, obj: dict) -> str | None:
        if resource_type not in ("policy_bindings", "flow_stage_bindings"):
            return None
        tgt = obj.get("target")
        if tgt is None:
            return None
        t = str(tgt)
        if t in self._pk_remap or t in self._live_pks or t in self._snap_pks:
            return None   # resolves live, or will once this run recreates its object
        return ("target object no longer exists anywhere - this binding was already "
                "orphaned when the snapshot was taken. Re-add it in Authentik if "
                "still needed.")

    def compare_form(self, resource_type: str, obj: dict) -> dict:
        # Compare with references remapped, so a binding pointing at a recreated
        # app's OLD id is identical to the live binding pointing at the NEW id.
        # Scalar lists (providers, property_mappings, ...) are semantically SETS —
        # Authentik returns them in arbitrary order — so sort them for comparison.
        out = self._remap_refs(dict(obj))
        for k, v in out.items():
            if isinstance(v, list) and v and all(isinstance(x, (str, int)) for x in v):
                out[k] = sorted(v, key=str)
        return out

    def _remap_refs(self, payload: dict) -> dict:
        if not self._pk_remap:
            return payload
        for f in self._REF_FIELDS:
            v = payload.get(f)
            if isinstance(v, (str, int)) and str(v) in self._pk_remap:
                payload[f] = self._pk_remap[str(v)]
            elif isinstance(v, list):
                payload[f] = [self._pk_remap.get(str(x), x) if isinstance(x, (str, int)) else x
                              for x in v]
        return payload

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
        # httpx REPLACES a URL's query string when params= is given - merge any
        # query baked into the path (e.g. superuser_full_list) instead.
        from urllib.parse import parse_qsl, urlsplit
        s = urlsplit(path)
        base_params = dict(parse_qsl(s.query))
        path = s.path
        out, page = [], 1
        while True:
            r = c.get(path, params={**base_params, "page": page, "page_size": 100})
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

    # Fallback write paths by RESOURCE TYPE — the non-polymorphic endpoints
    # (core/applications, policies/bindings, …) don't include meta_model_name
    # in their objects, so path resolution by model name alone fails for them.
    _RTYPE_PATHS = {
        "applications": "core/applications/",
        "groups": "core/groups/",
        "flows": "flows/instances/",
        "flow_stage_bindings": "flows/bindings/",
        "policy_bindings": "policies/bindings/",
        "brands": "core/brands/",
        "certificates": "crypto/certificatekeypairs/",
        "outposts": "outposts/instances/",
    }

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

    # Reference fields carry MEANING - dropping them to appease a 400 would
    # change what the object connects, and it hides the real error ("target
    # does not exist" became "target is required" once popped). Never pop.
    _NEVER_POP = {"target", "policy", "group", "user", "stage", "flow", "provider"}

    def _send(self, c, method: str, path: str, payload: dict):
        """Write with self-heal: on 400, drop the exact fields Authentik's error
        body names (DRF returns {"field": ["problem"]}) and retry — bounded.
        Reference fields are exempt: a 400 naming one is a REAL error."""
        payload = dict(payload)
        r = None
        for _ in range(6):
            r = c.request(method, path, json=payload)
            if r.status_code != 400:
                return r
            try:
                err = r.json()
            except Exception:
                return r
            bad = ([k for k in err.keys() if k in payload and k not in self._NEVER_POP]
                   if isinstance(err, dict) else [])
            if not bad:
                return r
            for k in bad:
                payload.pop(k, None)
        return r

    def push_object(self, resource_type: str, obj: dict, live: dict | None = None) -> tuple[str, str]:
        """Create-or-update one object from a snapshot. Returns (action, live_pk).
        Updates use PATCH (partial) so unchanged-but-strictly-validated fields on
        the live object can't fail a write that never intended to touch them."""
        if obj.get("managed"):
            return ("skipped_managed", str(obj.get("pk", "")))
        path = self._write_path(obj) or self._RTYPE_PATHS.get(resource_type)
        if not path:
            raise RuntimeError(f"no write path known for {resource_type} "
                               f"(model {obj.get('meta_model_name')!r})")
        payload = self._remap_refs({k: v for k, v in obj.items()
                                    if k not in self.READONLY_FIELDS})
        # Bindings whose target is gone from the live tenant (Authentik keeps
        # orphaned bindings when their object is deleted, so snapshots can carry
        # them) cannot be recreated via the API - fail with an honest message
        # instead of a cryptic 400.
        if resource_type in ("policy_bindings", "flow_stage_bindings") and self._live_pks:
            tgt = payload.get("target")
            if tgt is not None and str(tgt) not in self._live_pks:
                raise RuntimeError(
                    f"binding target {str(tgt)[:36]} does not exist in the live tenant "
                    "(this binding was already orphaned when the snapshot was taken). "
                    "Restore or recreate the object it points at first, then re-add "
                    "the binding in Authentik.")
        old_pk = obj.get("pk") or obj.get("brand_uuid")
        # Authentik detail routes for applications/flows are keyed by SLUG, not pk.
        lookup_field = {"applications": "slug", "flows": "slug"}.get(resource_type)
        if lookup_field:
            ident = (live or {}).get(lookup_field) or obj.get(lookup_field)
        else:
            ident = (live or {}).get("pk") or old_pk
        with self._client() as c:
            if ident is not None:
                probe = c.get(f"/api/v3/{path}{ident}/")
                if probe.status_code == 200:
                    r = self._send(c, "PATCH", f"/api/v3/{path}{ident}/", payload)
                    if r.status_code >= 400:
                        raise RuntimeError(f"PATCH {path}{ident}/ -> {r.status_code}: {r.text[:280]}")
                    return ("updated", str(ident))
            r = self._send(c, "POST", f"/api/v3/{path}", payload)
            if r.status_code >= 400:
                raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:280]}")
            new_pk = r.json().get("pk", "")
            if old_pk is not None and new_pk:
                # later objects in THIS run referencing the old pk follow the new one
                self._pk_remap[str(old_pk)] = new_pk
            if new_pk:
                self._live_pks.add(str(new_pk))
            return ("created", str(new_pk))

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
            # Authentik has no direct per-user app assignments (those buckets stay
            # empty); access is granted by policy bindings that reference a group or a
            # user. Capture those principal-referencing bindings so the UI can show a
            # real "Policy bindings" count instead of a structurally-zero Assignments
            # column. NOTE: a binding's `target` is a pbm_uuid that the public API does
            # not expose on applications/flows (verified against 2026.5.4), so bindings
            # can NOT be attributed to a specific app here - we count every binding
            # with a group/user principal instead. The bindings themselves are restored
            # via config backups, not identity restore.
            groups = self._paged(c, "/api/v3/core/groups/")
            group_ref = [{"id": g.get("pk"), "name": g.get("name")} for g in groups]
            apps = self._paged(c, "/api/v3/core/applications/?superuser_full_list=true")
            app_ref = [{"id": a.get("pk"), "name": a.get("name")} for a in apps]
            bindings = self._paged(c, "/api/v3/policies/bindings/")
            app_policy_bindings = [
                {"target": b.get("target"), "group_id": b.get("group"),
                 "user_id": b.get("user"), "order": b.get("order"),
                 "enabled": b.get("enabled")}
                for b in bindings
                if b.get("group") or b.get("user")
            ]
            return {"users": users, "group_memberships": memberships,
                    "app_group_assignments": [], "app_user_assignments_direct": [],
                    "app_policy_bindings": app_policy_bindings,
                    "group_ref": group_ref, "app_ref": app_ref}

    def _write(self, c, method, path, **kw):
        r = c.request(method, path, **kw)
        if r.status_code >= 400 and r.status_code not in (409,):
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r

    def apply_identities(self, snap: dict, only_keys=None, revert_keys=None) -> dict:
        """Additive restore: create missing users (by username), re-add group
        memberships. App access is governed by config policy bindings (restore config
        for that). Resolved by natural key so recreated-object ids don't break edges.
        revert_keys: usernames of EXISTING users whose fields (name, email,
        active, type, path, attributes) are reverted to snapshot values."""
        rep = {"users": {"created": 0, "reverted": 0, "existing": 0, "skipped": 0, "failed": []},
               "group_memberships": {"added": 0, "skipped": 0, "failed": []},
               "app_group_assignments": {"added": 0, "skipped": 0, "failed": []},
               "app_user_assignments_direct": {"added": 0, "skipped": 0, "failed": []}}
        with self._client() as c:
            live = self.export_identities()
            live_user = {u.get("username"): u.get("id") for u in live.get("users", []) if u.get("username")}
            live_uids = {str(u.get("id")) for u in live.get("users", []) if u.get("id") is not None}
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
                if str(u.get("id")) in live_uids:
                    rep["users"]["existing"] += 1   # renamed live (same pk) - revert, never duplicate
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

            # profile reverts — explicitly selected EXISTING users only (PATCH;
            # credentials are never touched - they aren't in snapshots anyway)
            if revert_keys:
                live_by_name = {u.get("username"): u for u in live.get("users", []) if u.get("username")}
                live_by_id = {str(u.get("id")): u for u in live.get("users", []) if u.get("id") is not None}
                for u in snap.get("users", []):
                    uname = u.get("username")
                    if not uname or uname not in revert_keys:
                        continue
                    # match by username; fall back to immutable pk (renamed user)
                    lv = live_by_name.get(uname) or live_by_id.get(str(u.get("id")))
                    if lv is None or not self.revertable_diff(u, lv):
                        continue
                    try:
                        body = {"username": uname, "name": u.get("name") or uname,
                                "email": u.get("email") or "",
                                "is_active": bool(u.get("is_active", True)),
                                "type": u.get("type") or "internal",
                                "path": u.get("path") or "users",
                                "attributes": u.get("attributes") or {}}
                        self._write(c, "PATCH", f"/api/v3/core/users/{lv.get('id')}/", json=body)
                        rep["users"]["reverted"] += 1
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
