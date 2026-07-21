"""Auth0 adapter. Auth: OAuth2 client-credentials — the stored credential is
"client_id:client_secret"; the adapter mints a short-lived Management API token
per run (cached for its lifetime) and calls the Management API with it.

Management API endpoints differ: some paginate (page/per_page), some are a single
object or fixed list that rejects pagination params. A few are feature-gated
(custom domains = paid) or deprecated (rules) and may be absent on a tenant; those
are recorded empty rather than failing the whole backup.
"""
import copy
import logging
import re
import time

import httpx

from app.providers.base import ProviderAdapter

log = logging.getLogger(__name__)

# Paginated list endpoints (page / per_page).
PAGED = {
    "clients": "/api/v2/clients",
    "connections": "/api/v2/connections",
    "resource_servers": "/api/v2/resource-servers",
    "roles": "/api/v2/roles",
    "rules": "/api/v2/rules",
    "actions": "/api/v2/actions/actions",
}
# Single-fetch endpoints — one object or a fixed list; NO pagination params.
SINGLE = {
    "tenant_settings": "/api/v2/tenants/settings",
    "custom_domains": "/api/v2/custom-domains",
    "branding": "/api/v2/branding",
}
# Feature-gated / deprecated — may 4xx on a tenant that lacks them; skip, don't fail.
OPTIONAL = {"rules", "custom_domains"}


class Auth0Adapter(ProviderAdapter):
    name = "auth0"
    supports_identity = True
    restore_order = ["resource_servers", "connections", "roles", "clients", "rules"]
    never_restore = {"tenant_settings", "branding", "custom_domains", "actions"}
    # restore write paths + id field per type; actions/singletons excluded above.
    _WRITE_PATH = {"clients": "/api/v2/clients", "connections": "/api/v2/connections",
                   "resource_servers": "/api/v2/resource-servers", "roles": "/api/v2/roles",
                   "rules": "/api/v2/rules"}
    _ID_FIELD = {"clients": "client_id"}   # everything else keys on "id"
    _READONLY = {"id", "client_id", "tenant", "global", "signing_keys", "is_system",
                 "created_at", "updated_at", "owners", "client_secret",
                 "callback_url_template"}
    _NATURAL_KEY = {"clients": "client_id", "connections": "name",
                    "resource_servers": "identifier", "roles": "name", "rules": "name"}

    def __init__(self, base_url: str, credentials: str):
        super().__init__(base_url, credentials)
        self._tok = None
        self._tok_exp = 0.0
        from app.providers.base import CallCounter
        self._rl = CallCounter()   # API-call counting for progress + estimates

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
            event_hooks={"request": [self._rl.count]},
        )

    def validate_credentials(self) -> bool:
        try:
            with self._client() as c:
                return c.get("/api/v2/tenants/settings").status_code == 200
        except Exception:
            return False

    def natural_key(self, resource_type: str, obj: dict) -> str:
        field = self._NATURAL_KEY.get(resource_type)
        if field and obj.get(field) is not None:
            return str(obj[field])
        return super().natural_key(resource_type, obj)

    def _req(self, c: httpx.Client, method: str, path: str, **kw):
        """Auth0 Management API call with retry on 429 (honours Retry-After)."""
        r = None
        for attempt in range(6):
            r = c.request(method, path, **kw)
            if r.status_code != 429:
                return r
            wait = r.headers.get("retry-after")
            time.sleep(min(float(wait) if wait else 2 ** attempt, 10))
        return r

    def _paged(self, c: httpx.Client, path: str) -> list[dict]:
        out, page = [], 0
        while True:
            r = self._req(c, "GET", path, params={"page": page, "per_page": 100})
            r.raise_for_status()
            body = r.json()
            items = body if isinstance(body, list) else body.get("actions", [body])
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1

    def _single(self, c: httpx.Client, path: str) -> list[dict]:
        r = self._req(c, "GET", path)
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, list) else [body]

    def _fetch(self, c, rtype, path, paged) -> list[dict]:
        try:
            return self._paged(c, path) if paged else self._single(c, path)
        except httpx.HTTPStatusError as e:
            if rtype in OPTIONAL and 400 <= e.response.status_code < 500:
                log.warning("auth0 export: %s unavailable (HTTP %s) — skipping",
                            rtype, e.response.status_code)
                return []
            raise

    def export(self) -> dict[str, list[dict]]:
        out = {}
        with self._client() as c:
            for rtype, path in PAGED.items():
                out[rtype] = self._fetch(c, rtype, path, True)
            for rtype, path in SINGLE.items():
                out[rtype] = self._fetch(c, rtype, path, False)
        return out

    def _write_with_strip(self, c, method, path, payload):
        """Write to Auth0, auto-dropping fields it rejects as 'Additional properties
        not allowed' (read-only/computed export fields) — including nested ones it
        reports as '... on property X' — one error round at a time, until it accepts
        the body. Returns the final response."""
        payload = copy.deepcopy(payload)
        r = None
        for _ in range(16):
            r = self._req(c, method, path, json=payload)
            if r.status_code != 400:
                return r
            try:
                msg = r.json().get("message", "")
            except Exception:
                msg = ""
            m = re.search(r"Additional properties not allowed:\s*([^']+)'"
                          r"(?:\s*on property\s+([\w.]+))?", msg)
            if not m:
                return r
            fields = [f.strip() for f in m.group(1).split(",")]
            target = payload
            if m.group(2):                       # nested: descend into the named parent
                for seg in m.group(2).split("."):
                    target = target.get(seg) if isinstance(target, dict) else None
            removed = False
            if isinstance(target, dict):
                for f in fields:
                    if target.pop(f, None) is not None:
                        removed = True
            if not removed:
                return r
        return r

    def push_object(self, resource_type: str, obj: dict, live: dict | None = None) -> tuple[str, str]:
        """Create-or-update one config object. When the engine matched a live object
        (by natural key) it's passed as `live` and we PATCH that object's CURRENT id;
        otherwise we POST a new one. Auth0 system objects are skipped. Auth0 rejects
        read-only/computed fields one at a time, so writes auto-strip them and retry."""
        base = self._WRITE_PATH.get(resource_type)
        if not base:
            raise NotImplementedError(f"auth0: restore not supported for {resource_type}")
        if obj.get("is_system"):
            return ("skipped_system", str(obj.get("id", "")))
        idf = self._ID_FIELD.get(resource_type, "id")
        payload = {k: v for k, v in obj.items() if k not in self._READONLY}
        with self._client() as c:
            if live is not None:
                live_id = live.get(idf) or live.get("id")
                r = self._write_with_strip(c, "PATCH", f"{base}/{live_id}", payload)
                if r.status_code >= 400:
                    raise RuntimeError(f"PATCH {base}/{live_id} -> {r.status_code}: {r.text[:280]}")
                return ("updated", str(live_id))
            r = self._write_with_strip(c, "POST", base, payload)
            if r.status_code >= 400:
                raise RuntimeError(f"POST {base} -> {r.status_code}: {r.text[:280]}")
            body = r.json()
            return ("created", str(body.get(idf) or body.get("id") or ""))

    # ---- Users & Access (identity) backup / restore ----
    # Auth0 model: users + ROLES and ORGANIZATIONS as the "group" buckets (each
    # group_ref entry carries kind: "role" | "org" so restore hits the right
    # endpoint). Auth0 has no user<->app assignment concept, so the app buckets
    # stay empty (per the ProviderAdapter contract).
    _USER_EXPORT_CAP = 1000  # Auth0 page pagination is hard-capped at 1000 records

    @staticmethod
    def _slim_user(u: dict) -> dict:
        ids = u.get("identities") or []
        conn = ids[0].get("connection") if ids else None
        return {"id": u.get("user_id"),
                "profile": {"login": u.get("email") or u.get("username") or u.get("user_id"),
                            "email": u.get("email"),
                            "displayName": u.get("name") or "",
                            "username": u.get("username")},
                "status": "BLOCKED" if u.get("blocked") else "ACTIVE",
                "connection": conn,
                "created": u.get("created_at")}

    def _plist(self, c, path: str, cap: int | None = None, **extra) -> list[dict]:
        """Page/per_page list pager; tolerates dict envelopes."""
        out, page = [], 0
        while True:
            r = self._req(c, "GET", path, params={"page": page, "per_page": 100, **extra})
            r.raise_for_status()
            items = r.json()
            if not isinstance(items, list):
                items = (items.get("users") or items.get("members")
                         or items.get("organizations") or items.get("roles") or [])
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1
            if cap and page * 100 >= cap:
                return out

    def export_identities(self) -> dict[str, list[dict]]:
        with self._client() as c:
            r = self._req(c, "GET", "/api/v2/users",
                          params={"page": 0, "per_page": 1, "include_totals": "true"})
            r.raise_for_status()
            total = r.json().get("total", 0)
            if total > self._USER_EXPORT_CAP:
                raise RuntimeError(
                    f"auth0: tenant has {total} users; the paged export is capped at "
                    f"{self._USER_EXPORT_CAP}. Bulk users-export job support is on the "
                    f"roadmap - refusing a silently partial backup.")
            users = [self._slim_user(u)
                     for u in self._plist(c, "/api/v2/users", cap=self._USER_EXPORT_CAP)]
            memberships, group_ref = [], []
            for rl in self._plist(c, "/api/v2/roles"):
                rid = rl.get("id")
                group_ref.append({"id": rid, "name": rl.get("name"), "kind": "role"})
                for m in self._plist(c, f"/api/v2/roles/{rid}/users", cap=self._USER_EXPORT_CAP):
                    memberships.append({"group_id": rid, "user_id": m.get("user_id")})
            try:
                orgs = self._plist(c, "/api/v2/organizations")
            except httpx.HTTPStatusError as e:   # feature-gated on some plans
                if 400 <= e.response.status_code < 500:
                    log.warning("auth0 identities: organizations unavailable (HTTP %s) - skipping",
                                e.response.status_code)
                    orgs = []
                else:
                    raise
            for o in orgs:
                oid = o.get("id")
                group_ref.append({"id": oid, "name": o.get("display_name") or o.get("name"),
                                  "kind": "org"})
                for m in self._plist(c, f"/api/v2/organizations/{oid}/members",
                                     cap=self._USER_EXPORT_CAP):
                    memberships.append({"group_id": oid, "user_id": m.get("user_id")})
            return {"users": users, "group_memberships": memberships,
                    "app_group_assignments": [], "app_user_assignments_direct": [],
                    "group_ref": group_ref, "app_ref": []}

    # email changes via the Auth0 API trigger verification side effects - never
    # part of a profile revert (login is the match key, so it can't differ anyway)
    _REVERT_EXCLUDE = ProviderAdapter._REVERT_EXCLUDE | {"email", "login"}

    def apply_identities(self, snap: dict, only_keys=None, revert_keys=None) -> dict:
        """Additive restore: recreate missing users (by email/login), then re-add
        missing role assignments and organization memberships. Resolved by natural
        key ((kind, name) for roles/orgs; login for users) so recreated-object id
        changes don't break edges. Recreated users are BLOCKED with a random
        password (Auth0 requires one) - admin sends a reset, then unblocks.
        revert_keys: logins of EXISTING users whose name/username revert to
        snapshot values (email deliberately excluded)."""
        import secrets as pysecrets
        rep = {"users": {"created": 0, "reverted": 0, "existing": 0, "skipped": 0, "failed": []},
               "group_memberships": {"added": 0, "skipped": 0, "failed": []},
               "app_group_assignments": {"added": 0, "skipped": 0, "failed": []},
               "app_user_assignments_direct": {"added": 0, "skipped": 0, "failed": []}}
        with self._client() as c:
            live = self.export_identities()
            live_user = {(u.get("profile") or {}).get("login"): u.get("id")
                         for u in live.get("users", [])}
            live_uids = {str(u.get("id")) for u in live.get("users", []) if u.get("id")}
            live_group = {(g.get("kind"), g.get("name")): g["id"]
                          for g in live.get("group_ref", []) if g.get("name")}
            live_group_ids = {g["id"] for g in live.get("group_ref", [])}
            snap_login = {u.get("id"): (u.get("profile") or {}).get("login")
                          for u in snap.get("users", [])}
            snap_gref = {g["id"]: g for g in snap.get("group_ref", [])}

            # 1) users - create the ones missing live (matched by email/login)
            for u in snap.get("users", []):
                prof = u.get("profile") or {}
                login = prof.get("login")
                if not login:
                    rep["users"]["failed"].append({"user": u.get("id"), "error": "user has no email/login"})
                    continue
                if login in live_user:
                    rep["users"]["existing"] += 1
                    continue
                if str(u.get("id")) in live_uids:
                    rep["users"]["existing"] += 1   # email changed live (same user_id) - never duplicate
                    continue
                if only_keys is not None and login not in only_keys:
                    rep["users"]["skipped"] += 1
                    continue
                conn = u.get("connection")
                if not conn:
                    rep["users"]["failed"].append({"user": login, "error": "no connection recorded in snapshot"})
                    continue
                payload = {"connection": conn, "email": prof.get("email"),
                           "name": prof.get("displayName") or None,
                           "blocked": True, "email_verified": True, "verify_email": False,
                           "password": pysecrets.token_urlsafe(24) + "aA1!"}
                if prof.get("username"):
                    payload["username"] = prof["username"]
                payload = {k: v for k, v in payload.items() if v is not None}
                r = self._req(c, "POST", "/api/v2/users", json=payload)
                if r.status_code == 400 and "username" in payload and "username" in (r.text or "").lower():
                    payload.pop("username")   # connection doesn't take usernames
                    r = self._req(c, "POST", "/api/v2/users", json=payload)
                if r.status_code == 409:
                    rep["users"]["existing"] += 1
                elif r.status_code >= 400:
                    err = r.text[:180]
                    if "connection" in err.lower():
                        err += " (only database-connection users can be recreated via the API; "\
                               "social/enterprise users sign in again through their IdP)"
                    rep["users"]["failed"].append({"user": login, "error": err})
                else:
                    live_user[login] = r.json().get("user_id")
                    rep["users"]["created"] += 1
                    self._rec_name(rep["users"], "created_names", login)

            # 1b) profile reverts — explicitly selected EXISTING users only.
            # Deliberately limited to name/username: email changes have
            # verification side effects and are excluded (see _REVERT_EXCLUDE).
            if revert_keys:
                from urllib.parse import quote
                live_by_login = {(u.get("profile") or {}).get("login"): u
                                 for u in live.get("users", [])}
                live_by_id = {str(u.get("id")): u for u in live.get("users", []) if u.get("id")}
                for u in snap.get("users", []):
                    prof = u.get("profile") or {}
                    login = prof.get("login")
                    if not login or login not in revert_keys:
                        continue
                    # match by login; fall back to immutable user_id
                    lv = live_by_login.get(login) or live_by_id.get(str(u.get("id")))
                    if lv is None or not self.revertable_diff(u, lv):
                        continue
                    payload = {"name": prof.get("displayName") or None}
                    if prof.get("username"):
                        payload["username"] = prof["username"]
                    payload = {k: v for k, v in payload.items() if v is not None}
                    if not payload:
                        continue
                    r = self._req(c, "PATCH", f"/api/v2/users/{quote(str(lv.get('id')), safe='')}",
                                  json=payload)
                    if r.status_code >= 400:
                        rep["users"]["failed"].append({"user": login, "error": r.text[:180]})
                    else:
                        rep["users"]["reverted"] += 1
                        self._rec_name(rep["users"], "reverted_names", login)

            # 2) role assignments + organization memberships (one bucket, kind-dispatched)
            live_mem = {(e["group_id"], e["user_id"]) for e in live.get("group_memberships", [])}

            def r_group(gid):
                g = snap_gref.get(gid) or {}
                key = (g.get("kind"), g.get("name"))
                if key in live_group:
                    return live_group[key], g.get("kind")
                return (gid if gid in live_group_ids else None), g.get("kind")

            for e in snap.get("group_memberships", []):
                lg, kind = r_group(e["group_id"])
                lu = live_user.get(snap_login.get(e["user_id"]))
                if not lg or not lu or (lg, lu) in live_mem:
                    rep["group_memberships"]["skipped"] += 1
                    continue
                try:
                    if kind == "org":
                        r = self._req(c, "POST", f"/api/v2/organizations/{lg}/members",
                                      json={"members": [lu]})
                    else:
                        r = self._req(c, "POST", f"/api/v2/roles/{lg}/users",
                                      json={"users": [lu]})
                    if r.status_code >= 400:
                        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
                    rep["group_memberships"]["added"] += 1
                    self._rec_name(rep["group_memberships"], "added_names",
                                   f"{snap_login.get(e['user_id']) or e['user_id']} in "
                                   f"{(snap_gref.get(e['group_id']) or {}).get('name') or e['group_id']}")
                except Exception as ex:
                    rep["group_memberships"]["failed"].append(
                        {"edge": f"{lg}/{lu}", "error": str(ex)[:150]})
        return rep
