"""User management — admin only (enforced via router dependency in main)."""
import secrets as pysecrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core import deploy
from app.core.security import hash_password, require_admin
from app.models.db import AuditLog, AuthSession, MfaTrust, SessionLocal, User

router = APIRouter(tags=["users"], dependencies=[Depends(require_admin)])


VALID_ROLES = ("admin", "user", "org_admin", "org_viewer")


@router.get("/users")
def list_users() -> list[dict]:
    from app.models.db import Org
    with SessionLocal() as db:
        org_names = {o.id: o.name for o in db.query(Org).all()}
        return [{"id": u.id, "username": u.username, "email": u.email, "role": u.role,
                 "org_id": u.org_id, "org_name": org_names.get(u.org_id),
                 "is_active": u.is_active, "pending_invite": bool(u.invite_token)}
                for u in db.query(User).order_by(User.id).all()]


class UserIn(BaseModel):
    username: str
    email: str
    role: str = "user"
    org_id: int | None = None    # required for org_admin / org_viewer roles
    password: str | None = None  # set directly instead of sending an invite


@router.post("/users")
def create_user(body: UserIn, request: Request) -> dict:
    from app.core import license as lic
    if not lic.can_add_user():
        raise HTTPException(402, "user limit reached for your license — the free "
                                 "Community tier includes a single admin account. "
                                 "Add a license in Settings → License to add users")
    if body.role not in VALID_ROLES:
        raise HTTPException(422, "role must be admin, user, org_admin, or org_viewer")
    org_id = None
    if body.role in ("org_admin", "org_viewer"):
        if not lic.has_feature("msp"):
            raise HTTPException(402, "org-scoped roles require an MSP license")
        if not body.org_id:
            raise HTTPException(422, "org_id is required for org-scoped roles")
        from app.models.db import Org
        with SessionLocal() as db:
            if db.get(Org, body.org_id) is None:
                raise HTTPException(404, "org not found")
        org_id = body.org_id
    if body.password is not None and len(body.password) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    direct = body.password is not None
    invite = None if direct else pysecrets.token_urlsafe(24)
    with SessionLocal() as db:
        if db.query(User).filter(User.username == body.username).first():
            raise HTTPException(409, "username already exists")
        u = User(username=body.username, email=body.email, role=body.role,
                 org_id=org_id, is_active=direct,
                 password_hash=hash_password(body.password) if direct else None,
                 invite_token=invite)
        db.add(u)
        db.add(AuditLog(actor=request.state.user["username"], action="user.create",
                        detail={"username": body.username, "role": body.role,
                                "method": "password" if direct else "invite"}))
        db.commit()
        uid = u.id
    if direct:
        return {"id": uid, "invite_link": None, "emailed": False}
    invite_link = f"/#invite={invite}"
    emailed = False
    try:
        from app.core.mailer import send_mail
        base = deploy.public_base(request)
        send_mail(body.email, "You've been invited to IdPVault",
                  f"An IdPVault account was created for you (username: {body.username}).\n\n"
                  f"Set your password here: {base}/#invite={invite}\n\n"
                  f"This link is single-use.")
        emailed = True
    except Exception:
        pass
    return {"id": uid, "invite_link": invite_link, "emailed": emailed}


class UserPatch(BaseModel):
    role: str | None = None
    org_id: int | None = None
    is_active: bool | None = None


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, request: Request) -> dict:
    from app.core import license as lic
    from app.models.db import Org
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "user not found")
        if u.username == request.state.user["username"]:
            raise HTTPException(422, "cannot modify your own account here")
        if body.role in VALID_ROLES:
            if body.role in ("org_admin", "org_viewer") and not lic.has_feature("msp"):
                raise HTTPException(402, "org-scoped roles require an MSP license")
            u.role = body.role
            if body.role in ("admin", "user"):
                u.org_id = None
        if body.org_id is not None:
            if db.get(Org, body.org_id) is None:
                raise HTTPException(404, "org not found")
            u.org_id = body.org_id
        if body.is_active is not None:
            u.is_active = body.is_active
            if not body.is_active:
                db.query(AuthSession).filter(AuthSession.user_id == u.id).delete()
        db.add(AuditLog(actor=request.state.user["username"], action="user.update",
                        detail={"username": u.username}))
        db.commit()
        return {"id": u.id}


@router.post("/users/{user_id}/reset-mfa")
def reset_mfa(user_id: int, request: Request) -> dict:
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "user not found")
        u.mfa_enabled = False
        u.mfa_secret_enc = None
        db.query(MfaTrust).filter(MfaTrust.user_id == u.id).delete()
        db.add(AuditLog(actor=request.state.user["username"], action="user.reset_mfa",
                        detail={"username": u.username}))
        db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/reset")
def reset_password(user_id: int, request: Request) -> dict:
    import secrets as pysecrets
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "user not found")
        token = pysecrets.token_urlsafe(24)
        u.invite_token = token
        u.is_active = True
        email = u.email
        db.add(AuditLog(actor=request.state.user["username"], action="user.password_reset",
                        detail={"username": u.username}))
        db.commit()
    link = f"/#invite={token}"
    emailed = False
    if email:
        try:
            from app.core.mailer import send_mail
            base = deploy.public_base(request)
            send_mail(email, "IdPVault password reset",
                      f"A password reset was requested for your IdPVault account "
                      f"(username: {u.username}).\n\nSet a new password:\n{base}/#invite={token}\n\n"
                      f"If you did not expect this, contact your administrator.")
            emailed = True
        except Exception:
            pass
    return {"reset_link": link, "emailed": emailed}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, request: Request) -> dict:
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "user not found")
        if u.username == request.state.user["username"]:
            raise HTTPException(422, "cannot delete yourself")
        name = u.username
        db.query(AuthSession).filter(AuthSession.user_id == u.id).delete()
        db.delete(u)
        db.add(AuditLog(actor=request.state.user["username"], action="user.delete",
                        detail={"username": name}))
        db.commit()
        return {"deleted": name}
