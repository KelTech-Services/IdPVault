"""Auth0 adapter. Auth: OAuth2 client-credentials — the stored credential is
"client_id:client_secret"; the adapter mints a short-lived Management API token
per run (cached for its lifetime) and calls the Management API with it.

Management API endpoints differ: some paginate (page/per_page), some are a single
object or fixed list that rejects pagination params. A few are feature-gated
(custom domains = paid) or deprecated (rules) and may be absent on a tenant; those
are recorded empty rather than failing the whole backup.
"""
import logging
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
    restore_order = ["resource_servers", "connections", "roles", "clients", "rules"]
    never_restore = {"tenant_settings", "branding", "custom_domains", "actions"}
    # restore write paths + id field per type; actions/singletons excluded above.
    _WRITE_PATH = {"clients": "/api/v2/clients", "connections": "/api/v2/connections",
                   "resource_servers": "/api/v2/resource-servers", "roles": "/api/v2/roles",
                   "rules": "/api/v2/rules"}
    _ID_FIELD = {"clients": "client_id"}   # everything else keys on "id"
    _READONLY = {"id", "client_id", "tenant", "global", "signing_keys", "is_system",
                 "created_at", "updated_at", "owners", "client_secret"}

    def __init__(self, base_url: str, credentials: str):
        super().__init__(base_url, credentials)
        self._tok = None
        self._tok_exp = 0.0

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
        )

    def validate_credentials(self) -> bool:
        try:
            with self._client() as c:
                return c.get("/api/v2/tenants/settings").status_code == 200
        except Exception:
            return False

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

    def _single(self, c: httpx.Client, path: str) -> list[dict]:
        r = c.get(path)
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

    def push_object(self, resource_type: str, obj: dict) -> tuple[str, str]:
        """Create-or-update one config object from a snapshot. PATCH to update an
        existing object, POST to create a missing one; Auth0 system objects are
        skipped. Returns (action, live_id)."""
        base = self._WRITE_PATH.get(resource_type)
        if not base:
            raise NotImplementedError(f"auth0: restore not supported for {resource_type}")
        if obj.get("is_system"):
            return ("skipped_system", str(obj.get("id", "")))
        idf = self._ID_FIELD.get(resource_type, "id")
        oid = obj.get(idf)
        payload = {k: v for k, v in obj.items() if k not in self._READONLY}
        with self._client() as c:
            if oid:
                live = c.get(f"{base}/{oid}")
                if live.status_code == 200:
                    r = c.patch(f"{base}/{oid}", json=payload)
                    if r.status_code >= 400:
                        raise RuntimeError(f"PATCH {base}/{oid} -> {r.status_code}: {r.text[:280]}")
                    return ("updated", str(oid))
            r = c.post(base, json=payload)
            if r.status_code >= 400:
                raise RuntimeError(f"POST {base} -> {r.status_code}: {r.text[:280]}")
            body = r.json()
            return ("created", str(body.get(idf) or body.get("id") or ""))
