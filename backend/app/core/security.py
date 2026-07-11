"""Password hashing (scrypt, stdlib) and DB-backed sessions."""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

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
