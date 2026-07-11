"""Open-core license verification. Licenses are Ed25519-signed tokens verified
OFFLINE against an embedded public key — the app never phones home. Without a
valid license the app runs in the free Community tier (1 tenant, no identity backup).

Token format:  base64url(payload_json) + "." + base64url(signature)
Payload fields: customer, tier, max_tenants (null = unlimited), features[], issued, expires (epoch seconds)
"""
import base64
import json
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# KelTech signing public key. The matching private key is held by KelTech only
# (never in this repo/image) and used by tools/mint_license.py to issue keys.
PUBLIC_KEY_B64 = "e9zJJlaHIJK8vwUMICggfzFf7wMeIlxcoKyltGp8aF0="

FREE = {"tier": "community", "max_tenants": 1, "features": [],
        "valid": False, "customer": None, "expires": None}


def _b64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(token: str) -> dict | None:
    """Return the license payload if the signature is valid and unexpired, else None."""
    try:
        payload_b64, sig_b64 = token.strip().split(".", 1)
        payload = _b64url(payload_b64)
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))
        pub.verify(_b64url(sig_b64), payload)          # raises InvalidSignature
        data = json.loads(payload)
        exp = data.get("expires")
        if exp and datetime.now(timezone.utc).timestamp() > float(exp):
            return None
        return data
    except (InvalidSignature, ValueError, Exception):
        return None


def _stored_token() -> str | None:
    from app.models.db import SessionLocal, Setting
    with SessionLocal() as db:
        row = db.get(Setting, "license")
        return (row.value.get("token") if row else None) or None


def current_license() -> dict:
    token = _stored_token()
    if not token:
        return dict(FREE)
    data = verify(token)
    if not data:
        return {**FREE, "invalid_present": True}
    return {"tier": data.get("tier", "pro"),
            "max_tenants": data.get("max_tenants"),      # None = unlimited
            "features": data.get("features", []),
            "valid": True, "customer": data.get("customer"),
            "expires": data.get("expires")}


def can_add_tenant(current_count: int) -> bool:
    m = current_license()["max_tenants"]
    return m is None or current_count < m


def has_feature(feature: str) -> bool:
    return feature in current_license().get("features", [])
