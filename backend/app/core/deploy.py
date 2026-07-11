"""Deployment / reverse-proxy awareness. When a canonical public_url is set in
settings, IdPVault uses it for email links, HTTPS/secure-cookie detection, and
(optionally) strict Host enforcement — instead of trusting request headers.
"""
from urllib.parse import urlparse


def _general() -> dict:
    from app.models.db import SessionLocal, Setting
    with SessionLocal() as db:
        row = db.get(Setting, "general")
        return dict(row.value) if row else {}


def public_base(request) -> str:
    """Canonical base URL for building links (no trailing slash)."""
    pu = _general().get("public_url")
    if pu:
        return pu.rstrip("/")
    return str(request.base_url).rstrip("/")


def is_secure(request) -> bool:
    """True if the public-facing connection is HTTPS (honours proxy header)."""
    if str(_general().get("public_url", "")).startswith("https://"):
        return True
    if request.headers.get("x-forwarded-proto", "").lower() == "https":
        return True
    return request.url.scheme == "https"


def host_allowed(request) -> bool:
    """Host allowlist check. True unless enforcement is on and the Host mismatches."""
    g = _general()
    if not g.get("enforce_host"):
        return True
    pu = g.get("public_url")
    if not pu:
        return True
    want = (urlparse(pu).hostname or "").lower()
    host = request.headers.get("host", "").split(":")[0].lower()
    return (not want) or host == want or host in ("localhost", "127.0.0.1")
