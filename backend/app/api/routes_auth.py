"""Login (with optional MFA), logout, session info, invite acceptance,
first-run setup, self-service password change + TOTP MFA management."""
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.core import crypto, deploy, security, totp
from app.models.db import AuditLog, MfaTrust, SessionLocal, Setting, User

router = APIRouter(tags=["auth"])
COOKIE = "idpvault_session"
TRUST_COOKIE = "idpvault_mfa_trust"


# ---------- first-run setup ----------
@router.get("/auth/status")
def status() -> dict:
    with SessionLocal() as db:
        return {"needs_setup": db.query(User).count() == 0}


class SetupIn(BaseModel):
    username: str
    password: str


@router.post("/auth/setup")
def setup(body: SetupIn, request: Request, response: Response) -> dict:
    if len(body.password) < 8 or not body.username.strip():
        raise HTTPException(422, "username required and password must be >= 8 chars")
    with SessionLocal() as db:
        if db.query(User).count() != 0:
            raise HTTPException(409, "setup already completed")
        u = User(username=body.username.strip(), email="", role="admin", is_active=True,
                 password_hash=security.hash_password(body.password))
        db.add(u)
        db.add(AuditLog(actor=body.username.strip(), action="auth.first_run_setup", detail={}))
        db.commit()
        token = security.create_session(db, u.id)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        secure=deploy.is_secure(request),
                        max_age=security.SESSION_DAYS * 86400)
    return {"username": u.username, "role": u.role}


# ---------- login / logout ----------
class LoginIn(BaseModel):
    username: str
    password: str
    totp: str | None = None


def _trust_days(db) -> int:
    row = db.get(Setting, "general")
    try:
        return int(row.value.get("mfa_trust_days") or 0) if row else 0
    except (TypeError, ValueError):
        return 0


def _login_policy(db):
    row = db.get(Setting, "general")
    cfg = row.value if row else {}
    def _i(k, d):
        try:
            return int(cfg.get(k) or d)
        except (TypeError, ValueError):
            return d
    return _i("login_max_attempts", 5), _i("login_lockout_minutes", 15)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    import secrets as pysecrets
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    new_trust = None
    with SessionLocal() as db:
        u = db.query(User).filter(User.username == body.username).first()

        if u and u.locked_until and u.locked_until > now:
            mins = int((u.locked_until - now).total_seconds()) // 60 + 1
            raise HTTPException(429, f"account temporarily locked — try again in {mins} min")

        def _register_fail():
            if u is None:
                return
            max_att, lock_min = _login_policy(db)
            u.failed_logins = (u.failed_logins or 0) + 1
            if u.failed_logins >= max_att:
                u.locked_until = now + timedelta(minutes=lock_min)
                u.failed_logins = 0
                db.add(AuditLog(actor=u.username, action="auth.account_locked",
                                detail={"minutes": lock_min}))
            db.commit()

        if u is None or not u.is_active or not security.verify_password(body.password, u.password_hash):
            _register_fail()
            raise HTTPException(401, "invalid credentials")

        if u.mfa_enabled:
            trusted = False
            tok = request.cookies.get(TRUST_COOKIE)
            if tok:
                tr = db.query(MfaTrust).filter(MfaTrust.token == tok,
                                               MfaTrust.user_id == u.id).first()
                if tr and tr.expires_at > now:
                    trusted = True
            if not trusted:
                if not body.totp:
                    return {"mfa_required": True}
                secret = crypto.decrypt(bytes.fromhex(u.mfa_secret_enc), crypto._master_key()).decode()
                if not totp.verify(secret, body.totp):
                    _register_fail()
                    raise HTTPException(401, "invalid MFA code")
                days = _trust_days(db)
                if days > 0:
                    t = pysecrets.token_urlsafe(32)
                    db.add(MfaTrust(user_id=u.id, token=t, expires_at=now + timedelta(days=days)))
                    new_trust = (t, days)

        u.failed_logins = 0
        u.locked_until = None
        token = security.create_session(db, u.id)
        db.add(AuditLog(actor=u.username, action="auth.login", detail={}))
        db.commit()
    secure = deploy.is_secure(request)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=secure,
                        max_age=security.SESSION_DAYS * 86400)
    if new_trust:
        response.set_cookie(TRUST_COOKIE, new_trust[0], httponly=True, samesite="lax",
                            secure=secure, max_age=new_trust[1] * 86400)
    return {"username": u.username, "role": u.role, "mfa_enabled": u.mfa_enabled}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    with SessionLocal() as db:
        security.destroy_session(db, request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request) -> dict:
    u = request.state.user
    with SessionLocal() as db:
        row = db.get(User, u["id"])
        return {**u, "mfa_enabled": bool(row and row.mfa_enabled),
                "time_format": (row.time_format if row else None) or "auto",
                "theme": (row.theme if row else None) or "dark"}


# ---------- self-service password ----------
class ChangePw(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/change-password")
def change_password(body: ChangePw, request: Request) -> dict:
    if len(body.new_password) < 8:
        raise HTTPException(422, "new password must be >= 8 chars")
    with SessionLocal() as db:
        u = db.get(User, request.state.user["id"])
        if not security.verify_password(body.current_password, u.password_hash):
            raise HTTPException(401, "current password is incorrect")
        u.password_hash = security.hash_password(body.new_password)
        db.add(AuditLog(actor=u.username, action="auth.change_password", detail={}))
        db.commit()
    return {"ok": True}


# ---------- self-service profile ----------
class ProfileIn(BaseModel):
    email: str | None = None
    time_format: str | None = None  # auto | 12 | 24
    theme: str | None = None        # dark | light


@router.post("/auth/profile")
def update_profile(body: ProfileIn, request: Request) -> dict:
    if body.time_format is not None and body.time_format not in ("auto", "12", "24"):
        raise HTTPException(422, "time_format must be auto, 12, or 24")
    if body.theme is not None and body.theme not in ("dark", "light"):
        raise HTTPException(422, "theme must be dark or light")
    with SessionLocal() as db:
        u = db.get(User, request.state.user["id"])
        if body.email is not None:
            u.email = body.email.strip()
        if body.time_format is not None:
            u.time_format = body.time_format
        if body.theme is not None:
            u.theme = body.theme
        db.add(AuditLog(actor=u.username, action="auth.profile_update", detail={}))
        db.commit()
        return {"email": u.email, "time_format": u.time_format, "theme": u.theme}


# ---------- forgot password (public) ----------
class ForgotIn(BaseModel):
    identifier: str  # username or email


@router.post("/auth/forgot")
def forgot(body: ForgotIn, request: Request) -> dict:
    import secrets as pysecrets
    ident = (body.identifier or "").strip()
    if ident:
        with SessionLocal() as db:
            u = db.query(User).filter(
                (User.username == ident) | (User.email == ident)).first()
            if u and u.email:
                token = pysecrets.token_urlsafe(24)
                u.invite_token = token
                db.add(AuditLog(actor=u.username, action="auth.forgot_password", detail={}))
                db.commit()
                try:
                    from app.core.mailer import send_mail
                    base = deploy.public_base(request)
                    send_mail(u.email, "IdPVault password reset",
                              f"A password reset was requested for your IdPVault account "
                              f"(username: {u.username}).\n\nSet a new password:\n"
                              f"{base}/#invite={token}\n\nIf you did not request this, ignore this email.")
                except Exception:
                    pass
    return {"ok": True}  # always 200 — never reveal whether an account exists


# ---------- self-service MFA (TOTP) ----------
@router.post("/auth/mfa/setup")
def mfa_setup(request: Request) -> dict:
    """Generate a new secret (stored, not yet enabled) + QR for enrollment."""
    with SessionLocal() as db:
        u = db.get(User, request.state.user["id"])
        secret = totp.new_secret()
        u.mfa_secret_enc = crypto.encrypt(secret.encode(), crypto._master_key()).hex()
        db.commit()
        uri = totp.provisioning_uri(secret, u.username)
        return {"secret": secret, "otpauth_uri": uri, "qr_svg": totp.qr_svg(uri)}


class MfaCode(BaseModel):
    code: str


@router.post("/auth/mfa/enable")
def mfa_enable(body: MfaCode, request: Request) -> dict:
    with SessionLocal() as db:
        u = db.get(User, request.state.user["id"])
        if not u.mfa_secret_enc:
            raise HTTPException(422, "run MFA setup first")
        secret = crypto.decrypt(bytes.fromhex(u.mfa_secret_enc), crypto._master_key()).decode()
        if not totp.verify(secret, body.code):
            raise HTTPException(401, "code did not verify — try again")
        u.mfa_enabled = True
        db.add(AuditLog(actor=u.username, action="auth.mfa_enabled", detail={}))
        db.commit()
    return {"mfa_enabled": True}


@router.post("/auth/mfa/disable")
def mfa_disable(body: MfaCode, request: Request) -> dict:
    with SessionLocal() as db:
        u = db.get(User, request.state.user["id"])
        if u.mfa_enabled and u.mfa_secret_enc:
            secret = crypto.decrypt(bytes.fromhex(u.mfa_secret_enc), crypto._master_key()).decode()
            if not totp.verify(secret, body.code):
                raise HTTPException(401, "code did not verify")
        u.mfa_enabled = False
        u.mfa_secret_enc = None
        db.query(MfaTrust).filter(MfaTrust.user_id == u.id).delete()
        db.add(AuditLog(actor=u.username, action="auth.mfa_disabled", detail={}))
        db.commit()
    return {"mfa_enabled": False}


# ---------- invite acceptance ----------
class InviteAccept(BaseModel):
    token: str
    password: str


@router.post("/auth/accept-invite")
def accept_invite(body: InviteAccept) -> dict:
    if len(body.password) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    with SessionLocal() as db:
        u = db.query(User).filter(User.invite_token == body.token).first()
        if u is None:
            raise HTTPException(404, "invalid or used invite token")
        u.password_hash = security.hash_password(body.password)
        u.is_active = True
        u.invite_token = None
        db.add(AuditLog(actor=u.username, action="auth.invite_accepted", detail={}))
        db.commit()
        return {"username": u.username}
