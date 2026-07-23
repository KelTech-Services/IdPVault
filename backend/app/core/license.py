"""Open-core license verification. Licenses are Ed25519-signed tokens verified
OFFLINE against an embedded public key. Activation keys (IDPV-...) are traded
for a signed, instance-bound entitlement via app/core/activation.py - the only
data that ever leaves the install is the license key and a random instance id.
Community tier, legacy full keys, and offline entitlement files never make any
network call. Without a valid license the app runs in the free Community tier
(1 tenant, no identity backup).

Token format:  base64url(payload_json) + "." + base64url(signature)
Payload: customer, tier, max_tenants (null = unlimited), features[], issued, expires

Lifecycle: a token is honored for GRACE_DAYS past its expiry ("grace" status, a
renewal-hiccup buffer), then the app downgrades to Community — strict but
NON-DESTRUCTIVE: nothing is deleted; paid actions go dormant. The entitled set
under a tenant cap is the OLDEST tenants (lowest id); the free tenant is the
oldest one and always stays fully live.
"""
import base64
import json
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# KelTech signing public key. The matching private key is held by KelTech only
# (never in this repo/image) and used by tools/mint_license.py to issue keys.
PUBLIC_KEY_B64 = "e9zJJlaHIJK8vwUMICggfzFf7wMeIlxcoKyltGp8aF0="

GRACE_DAYS = 3

FREE = {"tier": "community", "max_tenants": 1, "max_users": 1, "features": [],
        "valid": False, "customer": None, "expires": None,
        "status": "community", "days_left": None}


def _b64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(token: str) -> dict | None:
    """Return the license payload if the signature is valid and it is within
    expiry + grace. The payload gets a computed '_status' ('active' | 'grace')
    and '_days_left' (until hard cutoff = expires + grace)."""
    try:
        payload_b64, sig_b64 = token.strip().split(".", 1)
        payload = _b64url(payload_b64)
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))
        pub.verify(_b64url(sig_b64), payload)          # raises InvalidSignature
        data = json.loads(payload)
        if data.get("kind") == "entitlement":
            # Entitlements are product-stamped (one signing key serves every
            # KelTech app) and bound to ONE install. Legacy payloads (no kind)
            # skip both checks - grandfathered.
            if data.get("product") != "idpvault":
                return None
            from app.core.activation import instance_id
            if data.get("instance_id") != instance_id():
                return None
        exp = data.get("expires")
        if exp:
            now = datetime.now(timezone.utc).timestamp()
            hard_cutoff = float(exp) + GRACE_DAYS * 86400
            if now > hard_cutoff:
                return None
            data["_status"] = "active" if now <= float(exp) else "grace"
            data["_days_left"] = max(0, int((hard_cutoff - now) // 86400))
        else:
            data["_status"] = "active"
            data["_days_left"] = None
        return data
    except (InvalidSignature, ValueError, Exception):
        return None


def peek(token: str) -> dict | None:
    """UNVERIFIED payload parse - for error messaging only, never for gating."""
    try:
        return json.loads(_b64url(token.strip().split(".", 1)[0]))
    except Exception:
        return None


def _stored() -> dict:
    from app.models.db import SessionLocal, Setting
    with SessionLocal() as db:
        row = db.get(Setting, "license")
        return dict(row.value) if row else {}


def _stored_token() -> str | None:
    return _stored().get("token") or None


def current_license() -> dict:
    """Entitlements as configured RIGHT NOW: the installed token's payload if it
    verifies (with status/days_left), the FREE tier otherwise. 'invalid_present'
    flags a stored token that no longer verifies (bad or past grace)."""
    cur = _stored()
    token = cur.get("token") or None
    if not token:
        return dict(FREE)
    # kind: how this install is licensed. "activation" = IDPV key kept fresh by
    # the license server; "offline" = portal-issued entitlement file (never
    # phones home); "legacy" = classic full key (grandfathered, offline).
    kind = cur.get("kind") or (
        "offline" if (peek(token) or {}).get("kind") == "entitlement" else "legacy")
    extras = {"kind": kind, "refreshed": cur.get("refreshed"),
              "refresh_error": cur.get("refresh_error")}
    data = verify(token)
    if not data:
        return {**FREE, **extras, "status": "expired_or_invalid",
                "invalid_present": True}
    return {**extras,
            "license_key": data.get("license_key"),
            "tier": data.get("tier", "pro"),
            "max_tenants": data.get("max_tenants"),      # None = unlimited
            "max_users": data.get("max_users"),          # None = unlimited
            "features": data.get("features", []),
            "valid": True, "customer": data.get("customer"),
            "expires": data.get("expires"),
            "status": data.get("_status", "active"),
            "days_left": data.get("_days_left")}


def can_add_tenant(current_count: int) -> bool:
    m = current_license()["max_tenants"]
    return m is None or current_count < m


def has_feature(feature: str) -> bool:
    return feature in current_license().get("features", [])


def can_add_user() -> bool:
    """Free tier = exactly ONE account (the first-run admin). Licensed = up to
    max_users from the key (None = unlimited)."""
    m = current_license().get("max_users")
    if m is None:
        return True
    from app.models.db import SessionLocal, User
    with SessionLocal() as db:
        return db.query(User).count() < m


def entitled_tenant_ids() -> set[int] | None:
    """Tenant ids allowed to run paid actions (backup/restore) under the current
    cap — the OLDEST tenants (lowest id). None = all tenants entitled."""
    m = current_license()["max_tenants"]
    if m is None:
        return None
    from app.models.db import SessionLocal, Tenant
    with SessionLocal() as db:
        ids = [t.id for t in db.query(Tenant.id).order_by(Tenant.id).limit(m)]
    return set(ids)


def is_tenant_entitled(tenant_id: int) -> bool:
    ids = entitled_tenant_ids()
    return ids is None or tenant_id in ids
