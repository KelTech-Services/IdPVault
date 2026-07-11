"""Login, logout, session info, invite acceptance."""
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.core import security
from app.models.db import AuditLog, SessionLocal, User

router = APIRouter(tags=["auth"])
COOKIE = "idpvault_session"


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, response: Response) -> dict:
    with SessionLocal() as db:
        u = db.query(User).filter(User.username == body.username).first()
        if u is None or not u.is_active or not security.verify_password(body.password, u.password_hash):
            raise HTTPException(401, "invalid credentials")
        token = security.create_session(db, u.id)
        db.add(AuditLog(actor=u.username, action="auth.login", detail={}))
        db.commit()
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=security.SESSION_DAYS * 86400)
    return {"username": u.username, "role": u.role}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE, "")
    with SessionLocal() as db:
        security.destroy_session(db, token)
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request) -> dict:
    return request.state.user


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
