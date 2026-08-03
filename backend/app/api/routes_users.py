"""User management — admin only (enforced via router dependency in main)."""
import secrets as pysecrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core import deploy
from app.core.security import hash_password, require_admin
from app.models.db import AuditLog, AuthSession, MfaTrust, SessionLocal, User

router = APIRouter(tags=["users"], dependencies=[Depends(require_admin)])


VALID_ROLES = ("admin", "user", "org_admin", "org_viewer")


def _other_active_admins(db, than_user_id: int) -> int:
    return len([u for u in db.query(User).all()
                if u.role == "admin" and u.is_active is not False
                and u.id != than_user_id])


def _other_breakglass_admins(db, than_user_id: int) -> int:
    """Active break-glass admins WITH MFA, excluding one user - the guardrail
    that keeps SSO 'required' mode from ever locking the install out."""
    return len([u for u in db.query(User).all()
                if u.role == "admin" and u.is_active is not False
                and u.breakglass and u.mfa_enabled
                and u.id != than_user_id])


def _guard_last_breakglass(db, u: User) -> None:
    """Refuse anything that would remove the last way back in while SSO is
    required: disabling, deleting, demoting or resetting MFA on the only
    active break-glass admin."""
    from app.core import oidc
    if u.breakglass and oidc.mode() == "required" \
            and not _other_breakglass_admins(db, u.id):
        raise HTTPException(422, "SSO is set to required - at least one active "
                                 "break-glass admin with MFA must remain")


@router.get("/users")
def list_users() -> list[dict]:
    from app.models.db import Org
    with SessionLocal() as db:
        org_names = {o.id: o.name for o in db.query(Org).all()}
        return [{"id": u.id, "username": u.username, "email": u.email, "role": u.role,
                 "org_id": u.org_id, "org_name": org_names.get(u.org_id),
                 "is_active": u.is_active, "pending_invite": bool(u.invite_token),
                 "sso_user": bool(u.sso_user), "breakglass": bool(u.breakglass),
                 "external": bool(u.external), "mfa_enabled": bool(u.mfa_enabled)}
                for u in db.query(User).order_by(User.id).all()]


class UserIn(BaseModel):
    username: str
    email: str
    role: str = "user"
    org_id: int | None = None    # required for org_admin / org_viewer roles
    password: str | None = None  # set directly instead of sending an invite
    breakglass: bool = False     # keeps password sign-in when SSO is required
    external: bool = False       # client contact, not in your directory


@router.post("/users")
def create_user(body: UserIn, request: Request) -> dict:
    from app.core import license as lic
    if not lic.can_add_user():
        raise HTTPException(402, "user limit reached for your license - the free "
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
    if body.breakglass and body.role != "admin":
        raise HTTPException(422, "only an administrator can be a break-glass account")
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
                 invite_token=invite,
                 breakglass=body.breakglass or None,
                 # org-scoped roles are client people by definition
                 external=(body.external
                           or body.role in ("org_admin", "org_viewer")) or None)
        db.add(u)
        db.add(AuditLog(actor=request.state.user["username"], action="user.create",
                        detail={"username": body.username, "role": body.role,
                                "breakglass": body.breakglass,
                                "external": bool(u.external),
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
    breakglass: bool | None = None
    external: bool | None = None


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
            if body.role != "admin" and u.role == "admin":
                if not _other_active_admins(db, u.id):
                    raise HTTPException(422, "cannot demote the last active administrator")
                _guard_last_breakglass(db, u)
            if body.role != "admin" and u.breakglass and body.breakglass is not False:
                raise HTTPException(422, "clear break-glass before changing this "
                                         "account's role - only administrators "
                                         "can be break-glass accounts")
            u.role = body.role
            if body.role in ("admin", "user"):
                u.org_id = None
            if body.role in ("org_admin", "org_viewer"):
                u.external = True
        if body.org_id is not None:
            if db.get(Org, body.org_id) is None:
                raise HTTPException(404, "org not found")
            u.org_id = body.org_id
        if body.breakglass is not None:
            if not body.breakglass and u.breakglass:
                _guard_last_breakglass(db, u)
            if body.breakglass and u.role != "admin":
                raise HTTPException(422, "only an administrator can be a "
                                         "break-glass account")
            u.breakglass = bool(body.breakglass) or None
        if body.external is not None:
            u.external = bool(body.external) or None
        if body.is_active is not None:
            if not body.is_active:
                active = [x for x in db.query(User).all() if x.is_active is not False]
                if len(active) <= 1:
                    raise HTTPException(422, "cannot disable the last active account")
                if u.role == "admin" and not _other_active_admins(db, u.id):
                    raise HTTPException(422, "cannot disable the last active administrator")
                _guard_last_breakglass(db, u)
            u.is_active = body.is_active
            if not body.is_active:
                db.query(AuthSession).filter(AuthSession.user_id == u.id).delete()
        db.add(AuditLog(actor=request.state.user["username"], action="user.update",
                        detail={"username": u.username,
                                **body.model_dump(exclude_unset=True)}))
        db.commit()
        return {"id": u.id}


@router.post("/users/{user_id}/reset-mfa")
def reset_mfa(user_id: int, request: Request) -> dict:
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "user not found")
        # The last break-glass admin must keep MFA - that pair IS the way back
        # in when the identity provider is down.
        _guard_last_breakglass(db, u)
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
        if u.role == "admin" and u.is_active is not False \
                and not _other_active_admins(db, u.id):
            raise HTTPException(422, "cannot delete the last active administrator")
        _guard_last_breakglass(db, u)
        name = u.username
        db.query(AuthSession).filter(AuthSession.user_id == u.id).delete()
        db.delete(u)
        db.add(AuditLog(actor=request.state.user["username"], action="user.delete",
                        detail={"username": name}))
        db.commit()
        return {"deleted": name}
