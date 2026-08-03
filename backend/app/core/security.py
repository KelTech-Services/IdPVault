"""Password hashing (scrypt, stdlib) and DB-backed sessions."""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import func

SESSION_DAYS = 30
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return salt.hex() + "$" + h.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, h_hex = stored.split("$", 1)
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=_N, r=_R, p=_P)
        return secrets.compare_digest(h.hex(), h_hex)
    except Exception:
        return False


def create_session(db, user_id: int) -> str:
    from app.models.db import AuthSession
    token = secrets.token_urlsafe(40)
    db.add(AuthSession(token=token, user_id=user_id,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)))
    db.commit()
    return token


def resolve_session(db, token: str):
    """Return the active user for a session token, or None."""
    from app.models.db import AuthSession, User
    if not token:
        return None
    s = db.query(AuthSession).filter(AuthSession.token == token).first()
    if s is None or s.expires_at < datetime.now(timezone.utc):
        return None
    u = db.get(User, s.user_id)
    if u is None or not u.is_active:
        return None
    return u


def destroy_session(db, token: str) -> None:
    from app.models.db import AuthSession
    db.query(AuthSession).filter(AuthSession.token == token).delete()
    db.commit()


def require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        raise HTTPException(403, "admin role required")


# ---------- MSP org scoping ----------
# Roles: admin (global, everything) / user (global, read-only) /
#        org_admin (backup, restore, edit tenants inside own org) /
#        org_viewer (read-only inside own org).
ORG_ROLES = ("org_admin", "org_viewer")
MSP_CONTACT_MSG = "contact your MSP administrator to take this action"


def visible_tenant_ids(db, user) -> set[int] | None:
    """None = unrestricted (global roles). For org-scoped users, the set of
    tenant ids inside their org (empty set if no org assigned)."""
    if not user or user.get("role") not in ORG_ROLES:
        return None
    if not user.get("org_id"):
        return set()
    from app.models.db import Tenant
    return {t.id for t in db.query(Tenant).filter(Tenant.org_id == user["org_id"]).all()}


def require_tenant_read(request: Request, db, tenant_id: int) -> None:
    """Global roles pass; org users must have the tenant in their org.
    404 (not 403) so tenant existence isn't leaked across orgs."""
    vis = visible_tenant_ids(db, getattr(request.state, "user", None))
    if vis is not None and tenant_id not in vis:
        raise HTTPException(404, "tenant not found")


def require_tenant_write(request: Request, db, tenant_id: int) -> None:
    """admin: always. org_admin: inside own org. Everyone else: 403."""
    user = getattr(request.state, "user", None) or {}
    role = user.get("role")
    if role == "admin":
        return
    if role == "org_admin":
        vis = visible_tenant_ids(db, user)
        if vis is None or tenant_id in vis:
            return
        raise HTTPException(404, "tenant not found")
    raise HTTPException(403, MSP_CONTACT_MSG)


# ---------- corporate identity (v1.4) ----------
# Email is the sign-in identifier. username stays as the internal/legacy
# handle so that accounts predating this release keep working unchanged.

def normalize_email(email: str | None) -> str:
    """Emails are compared and stored lowercase. '' means 'no email set'."""
    return (email or "").strip().lower()


def email_taken(db, email: str, exclude_id: int | None = None) -> bool:
    """Case-insensitive uniqueness, enforced HERE rather than by a DB index -
    legacy installs have a first-run admin with email='' and a unique index
    could not be built over them. An empty email is never 'taken'."""
    from app.models.db import User
    email = normalize_email(email)
    if not email:
        return False
    q = db.query(User).filter(func.lower(User.email) == email)
    if exclude_id is not None:
        q = q.filter(User.id != exclude_id)
    return q.first() is not None


def username_from_email(db, email: str) -> str:
    """users.username is varchar(80) + unique; emails can be longer or collide
    after truncation. Derive a safe internal username for IdP-created users."""
    from app.models.db import User
    base = normalize_email(email)[:80] or "user"
    name, n = base, 2
    while db.query(User).filter(User.username == name).first():
        suffix = str(n)
        name = base[:80 - len(suffix)] + suffix
        n += 1
    return name


def display_name(u) -> str:
    """The one true user label: 'First Last (email)'. Falls back to the
    username for legacy accounts with no name or email set - which includes
    the first-run admin on every install that predates this release."""
    name = " ".join(x for x in [(u.first_name or "").strip(),
                                (u.last_name or "").strip()] if x) or u.username
    return f"{name} ({u.email})" if u.email else name
