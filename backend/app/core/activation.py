"""Activation licensing client (v1.3.0). Talks to the KelTech license server
for ACTIVATION KEYS only (IDPV-XXXX-XXXX-XXXX-XXXX). The server returns a
signed entitlement token bound to this install's instance id; the app then
verifies that token OFFLINE with the same embedded public key as always
(app/core/license.py). The ONLY data ever sent is the license key and the
random instance id - never tenant data, never usage.

Paths that NEVER touch the network:
  - Community tier (no key installed)
  - legacy full keys (grandfathered, verified offline)
  - offline entitlement files (downloaded from the customer portal,
    instance-bound, verified offline)
"""
import logging
import os
import re
import secrets
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

LICENSE_SERVER = os.environ.get("IDPVAULT_LICENSE_SERVER",
                                "https://license.keltech.ai")
# Same alphabet the server mints keys from (no 0/O/1/I/L).
_KEY_RE = re.compile(r"^IDPV(-[A-Z0-9]{4}){4}$")
_ID_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def norm_key(s: str) -> str:
    return (s or "").strip().upper()


def is_activation_key(s: str) -> bool:
    return bool(_KEY_RE.match(norm_key(s)))


def instance_id() -> str:
    """Stable random id for THIS install, created on first use and stored in
    the DB (survives container recreation with the data volume). Shown on the
    License page; typed into the customer portal for offline files."""
    from app.models.db import SessionLocal, Setting
    with SessionLocal() as db:
        row = db.get(Setting, "instance")
        if row and row.value.get("id"):
            return row.value["id"]
        iid = "-".join("".join(secrets.choice(_ID_ALPHABET) for _ in range(4))
                       for _ in range(3))
        if row is None:
            db.add(Setting(key="instance", value={"id": iid}))
        else:
            row.value = {**dict(row.value), "id": iid}
        db.commit()
        return iid


class ActivationError(Exception):
    """Server said no (carries the human-readable reason and HTTP status)."""
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class ServerUnreachable(Exception):
    pass


def _user_agent() -> str:
    # A real User-Agent is REQUIRED: the license server sits behind Cloudflare
    # and default python client UAs are bot-blocked.
    try:
        from app.main import app
        ver = app.version
    except Exception:
        ver = "unknown"
    return f"IdPVault/{ver} (+https://idpvault.com)"


def _post(path: str, key: str) -> dict:
    """POST key + instance id, return parsed JSON. Raises ActivationError on a
    4xx/5xx with the server's reason, ServerUnreachable on network trouble."""
    try:
        r = httpx.post(LICENSE_SERVER.rstrip("/") + path,
                       json={"key": norm_key(key), "instance_id": instance_id()},
                       headers={"User-Agent": _user_agent()}, timeout=20)
    except Exception as e:
        raise ServerUnreachable(str(e))
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or f"license server error {r.status_code}"
        except Exception:
            detail = f"license server error {r.status_code}"
        raise ActivationError(detail, r.status_code)
    return r.json()


def _store(value: dict) -> None:
    from app.models.db import SessionLocal, Setting
    with SessionLocal() as db:
        row = db.get(Setting, "license")
        if row is None:
            db.add(Setting(key="license", value=value))
        else:
            row.value = value
        db.commit()


def stored() -> dict:
    from app.models.db import SessionLocal, Setting
    with SessionLocal() as db:
        row = db.get(Setting, "license")
        return dict(row.value) if row else {}


def activate(key: str) -> dict:
    """Activate an IDPV key against the license server and store the returned
    entitlement. Returns the server response."""
    resp = _post("/v1/activate", key)
    _store({"kind": "activation", "key": norm_key(key),
            "token": resp["entitlement"],
            "refreshed": int(datetime.now(timezone.utc).timestamp()),
            "refresh_error": None})
    return resp


def deactivate_remote(key: str) -> bool:
    """Best-effort release on the server (idempotent there). Returns True if
    the server acknowledged; False if unreachable/refused - the local license
    is cleared by the caller regardless."""
    try:
        _post("/v1/deactivate", key)
        return True
    except (ActivationError, ServerUnreachable) as e:
        log.warning("license deactivate (server-side) failed: %s", e)
        return False


def refresh() -> None:
    """Daily job + boot: re-issue the entitlement for activation-kind licenses
    (picks up renewals and add-on changes). Community / legacy / offline-file
    installs return immediately - zero network. Failures are quiet: the stored
    entitlement stays valid until its expiry + grace."""
    cur = stored()
    if cur.get("kind") != "activation" or not cur.get("key"):
        return
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        resp = _post("/v1/refresh", cur["key"])
        _store({**cur, "token": resp["entitlement"], "refreshed": now,
                "refresh_error": None})
        log.info("license entitlement refreshed")
    except ActivationError as e:
        # 409 = another install took the activation; 403 = revoked/expired.
        # Record it for the UI; the entitlement lapses on its own schedule.
        _store({**cur, "refresh_error": str(e)})
        log.warning("license refresh refused (%s): %s", e.status, e)
    except ServerUnreachable as e:
        log.info("license server unreachable for refresh (will retry): %s", e)
