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
    restore_order = ["groups", "network_zones", "policies_password", "policies_mfa",
                     "policies_signon", "policies_access", "idps", "event_hooks", "inline_hooks"]
    # Apps and schema-shaped types are deliberately not auto-restored (server-generated
    # structure / lifecycle endpoints); they stay backed up and browsable.
    never_restore = {"apps", "authorization_servers", "user_schemas", "user_types",
                     "user_type_schemas", "app_user_schemas", "profile_mappings"}
    _WRITE_PATH = {"groups": "/api/v1/groups", "network_zones": "/api/v1/zones",
                   "policies_signon": "/api/v1/policies", "policies_password": "/api/v1/policies",
                   "policies_mfa": "/api/v1/policies", "policies_access": "/api/v1/policies",
                   "idps": "/api/v1/idps", "event_hooks": "/api/v1/eventHooks",
                   "inline_hooks": "/api/v1/inlineHooks"}
    _READONLY = {"id", "created", "lastUpdated", "lastMembershipUpdated",
                 "_links", "_embedded", "system"}

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

            group_ref = [{"id": g.get("id"), "name": (g.get("profile") or {}).get("name")}
                         for g in groups]
            app_ref = [{"id": a.get("id"), "label": a.get("label")} for a in apps]
            return {"users": users, "group_memberships": memberships,
                    "app_group_assignments": app_group,
                    "app_user_assignments_direct": app_user_direct,
                    "group_ref": group_ref, "app_ref": app_ref}


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

    # ---- config restore (apply / write) ----
    def push_object(self, resource_type: str, obj: dict, live: dict | None = None) -> tuple[str, str]:
        """Create-or-update one config object. When the engine matched a live object
        it's passed as `live` and we PUT (full replace) at its CURRENT id; otherwise
        POST a new one. Okta system objects (system policies/zones, built-in and
        app-sourced groups) are skipped — they can't be user-written."""
        base = self._WRITE_PATH.get(resource_type)
        if not base:
            raise NotImplementedError(f"okta: restore not supported for {resource_type}")
        if obj.get("system"):
            return ("skipped_system", str(obj.get("id", "")))
        if resource_type == "groups":
            if obj.get("type") and obj["type"] != "OKTA_GROUP":
                return ("skipped_system", str(obj.get("id", "")))  # BUILT_IN / APP_GROUP
            payload = {"profile": obj.get("profile", {})}          # only profile is writable
        else:
            payload = {k: v for k, v in obj.items() if k not in self._READONLY}
        with self._client() as c:
            if live is not None:
                live_id = live.get("id")
                r = self._rl.request(lambda: c.put(f"{base}/{live_id}", json=payload))
                if r.status_code >= 400:
                    raise RuntimeError(f"PUT {base}/{live_id} -> {r.status_code}: {r.text[:280]}")
                return ("updated", str(live_id))
            r = self._rl.request(lambda: c.post(base, json=payload))
            if r.status_code >= 400:
                raise RuntimeError(f"POST {base} -> {r.status_code}: {r.text[:280]}")
            return ("created", str(r.json().get("id", "")))

    # ---- identity restore (apply / write) ----
    def _write(self, c, method, path, **kw):
        r = self._rl.request(lambda: c.request(method, path, **kw))
        if r.status_code >= 400 and r.status_code not in (409,):
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r

    def apply_identities(self, snap: dict, only_keys=None) -> dict:
        """Additive restore: recreate missing users (by login), then re-add missing
        memberships / group->app / direct user->app edges. Everything resolved by
        NATURAL KEY (login / group name / app label) so recreated-object id changes
        don't break edges. Idempotent: existing users/edges are skipped."""
        rep = {"users": {"created": 0, "existing": 0, "skipped": 0, "failed": []},
               "group_memberships": {"added": 0, "skipped": 0, "failed": []},
               "app_group_assignments": {"added": 0, "skipped": 0, "failed": []},
               "app_user_assignments_direct": {"added": 0, "skipped": 0, "failed": []}}
        with self._client() as c:
            live = self.export_identities()
            live_user = {(u.get("profile") or {}).get("login"): u.get("id")
                         for u in live.get("users", []) if (u.get("profile") or {}).get("login")}
            live_group = {g["name"]: g["id"] for g in live.get("group_ref", []) if g.get("name")}
            live_group_ids = {g["id"] for g in live.get("group_ref", [])}
            live_app = {a["label"]: a["id"] for a in live.get("app_ref", []) if a.get("label")}
            live_app_ids = {a["id"] for a in live.get("app_ref", [])}

            snap_user_login = {u.get("id"): (u.get("profile") or {}).get("login") for u in snap.get("users", [])}
            snap_group_name = {g["id"]: g["name"] for g in snap.get("group_ref", [])}
            snap_app_label = {a["id"]: a["label"] for a in snap.get("app_ref", [])}

            def r_user(uid):
                return live_user.get(snap_user_login.get(uid))

            def r_group(gid):
                name = snap_group_name.get(gid)
                if name and name in live_group:
                    return live_group[name]
                return gid if gid in live_group_ids else None  # fallback: stable id

            def r_app(aid):
                label = snap_app_label.get(aid)
                if label and label in live_app:
                    return live_app[label]
                return aid if aid in live_app_ids else None

            # 1) users — create the ones missing live (matched by login)
            for u in snap.get("users", []):
                login = (u.get("profile") or {}).get("login")
                if not login:
                    rep["users"]["failed"].append({"user": u.get("id"), "error": "user has no login"})
                    continue
                if login in live_user:
                    rep["users"]["existing"] += 1
                    continue
                if only_keys is not None and login not in only_keys:
                    rep["users"]["skipped"] += 1
                    continue
                try:
                    r = self._write(c, "POST", "/api/v1/users", params={"activate": "false"},
                                    json={"profile": u.get("profile", {})})
                    if r.status_code == 409:
                        rep["users"]["existing"] += 1
                    else:
                        live_user[login] = r.json().get("id")
                        rep["users"]["created"] += 1
                except Exception as e:
                    rep["users"]["failed"].append({"user": login, "error": str(e)[:200]})

            live_mem = {(e["group_id"], e["user_id"]) for e in live.get("group_memberships", [])}
            live_ag = {(e["app_id"], e["group_id"]) for e in live.get("app_group_assignments", [])}
            live_au = {(e["app_id"], e["user_id"]) for e in live.get("app_user_assignments_direct", [])}

            # 2) group memberships
            for e in snap.get("group_memberships", []):
                lg, lu = r_group(e["group_id"]), r_user(e["user_id"])
                if not lg or not lu:
                    rep["group_memberships"]["skipped"] += 1
                    continue
                if (lg, lu) in live_mem:
                    rep["group_memberships"]["skipped"] += 1
                    continue
                try:
                    self._write(c, "PUT", f"/api/v1/groups/{lg}/users/{lu}")
                    rep["group_memberships"]["added"] += 1
                except Exception as ex:
                    rep["group_memberships"]["failed"].append({"edge": f"{lg}/{lu}", "error": str(ex)[:150]})

            # 3) group->app assignments
            for e in snap.get("app_group_assignments", []):
                la, lg = r_app(e["app_id"]), r_group(e["group_id"])
                if not la or not lg:
                    rep["app_group_assignments"]["skipped"] += 1
                    continue
                if (la, lg) in live_ag:
                    rep["app_group_assignments"]["skipped"] += 1
                    continue
                try:
                    self._write(c, "PUT", f"/api/v1/apps/{la}/groups/{lg}")
                    rep["app_group_assignments"]["added"] += 1
                except Exception as ex:
                    rep["app_group_assignments"]["failed"].append({"edge": f"{la}/{lg}", "error": str(ex)[:150]})

            # 4) direct user->app assignments (scope USER only — provenance preserved)
            for e in snap.get("app_user_assignments_direct", []):
                la, lu = r_app(e["app_id"]), r_user(e["user_id"])
                if not la or not lu:
                    rep["app_user_assignments_direct"]["skipped"] += 1
                    continue
                if (la, lu) in live_au:
                    rep["app_user_assignments_direct"]["skipped"] += 1
                    continue
                try:
                    self._write(c, "POST", f"/api/v1/apps/{la}/users",
                                json={"id": lu, "scope": "USER"})
                    rep["app_user_assignments_direct"]["added"] += 1
                except Exception as ex:
                    rep["app_user_assignments_direct"]["failed"].append({"edge": f"{la}/{lu}", "error": str(ex)[:150]})
        return rep
