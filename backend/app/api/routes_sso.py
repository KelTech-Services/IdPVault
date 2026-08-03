"""SSO sign-in routes (OIDC today; the SAML branch dispatches from here).

Two public endpoints - start and callback - plus the admin config API.
Nothing here is reachable unless the license carries the "identity" feature
and an admin has configured and enabled SSO.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.core import crypto, deploy, oidc, security
from app.core.security import require_admin
from app.models.db import AuditLog, SessionLocal, User

router = APIRouter(tags=["sso"])
COOKIE = "idpvault_session"


def _redirect_uri(request: Request) -> str:
    """The IdP redirect target. MUST match what is registered at the IdP
    exactly, including scheme and host, which is why it is derived from the
    proxy headers rather than hardcoded."""
    return deploy.public_base(request).rstrip("/") + "/api/v1/auth/sso/callback"


@router.get("/auth/sso/redirect-uri", dependencies=[Depends(require_admin)])
def redirect_uri(request: Request) -> dict:
    """Shown in Settings so an admin can copy it into the IdP."""
    return {"redirect_uri": _redirect_uri(request)}


@router.get("/auth/sso/public")
def sso_public() -> dict:
    """Unauthenticated: whether to show the SSO button, and its label.
    Never exposes any part of the configuration."""
    return oidc.public_info()


@router.get("/auth/sso/login")
def sso_login(request: Request):
    """Start the flow: redirect to the IdP and drop the encrypted txn cookie."""
    if oidc.mode() == "off":
        raise HTTPException(404, "single sign-on is not enabled")
    try:
        url, txn = oidc.auth_request(_redirect_uri(request))
    except Exception as e:
        raise HTTPException(502, f"could not reach the identity provider: {e}")
    r = RedirectResponse(url, status_code=302)
    # SameSite=lax is correct for OIDC: the IdP sends the user back with a GET
    # redirect, which lax allows. (SAML's POST binding is the case that needs
    # SameSite=none - handled separately when that lands.)
    r.set_cookie(oidc.TXN_COOKIE, txn, httponly=True, samesite="lax",
                 secure=deploy.is_secure(request), max_age=oidc.TXN_MAX_AGE)
    return r


def _fail(msg: str):
    """Errors land back on the login screen, readable, never a raw traceback."""
    from urllib.parse import quote
    return RedirectResponse(f"/#sso_error={quote(msg[:200])}", status_code=302)


@router.get("/auth/sso/callback")
def sso_callback(request: Request, code: str = "", state: str = "",
                 error: str = "", error_description: str = ""):
    if oidc.mode() == "off":
        raise HTTPException(404, "single sign-on is not enabled")
    if error:
        return _fail(error_description or error)
    raw = request.cookies.get(oidc.TXN_COOKIE, "")
    if not raw or not code:
        return _fail("this sign-in link is incomplete - start again")
    try:
        txn = oidc.read_txn_cookie(raw)
    except Exception:
        return _fail("this sign-in attempt expired - try again")
    if not state or state != txn.get("s"):
        return _fail("sign-in state did not match - try again")
    try:
        claims = oidc.exchange(code, txn["v"], txn["n"], _redirect_uri(request))
    except ValueError as e:
        return _fail(str(e))
    except Exception:
        return _fail("could not complete sign-in with the identity provider")

    with SessionLocal() as db:
        try:
            u, created = oidc.resolve_user(db, claims)
        except ValueError as e:
            return _fail(str(e))
        token = security.create_session(db, u.id)
        db.add(AuditLog(actor=u.username,
                        action="auth.sso_signup" if created else "auth.sso_login",
                        detail={"email": u.email, "protocol": oidc.protocol()}))
        db.commit()
    r = RedirectResponse("/", status_code=302)
    secure = deploy.is_secure(request)
    r.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=secure,
                 max_age=security.SESSION_DAYS * 86400)
    r.delete_cookie(oidc.TXN_COOKIE)
    return r


# ---------- admin config ----------

class SsoIn(BaseModel):
    mode: str = "off"
    protocol: str = "oidc"               # oidc | saml
    issuer: str | None = None
    client_id: str | None = None
    client_secret: str | None = None     # blank on save = keep the stored one
    scopes: str | None = None
    button_label: str | None = None
    jit_default_role: str | None = None
    saml_metadata_url: str | None = None


@router.get("/sso", dependencies=[Depends(require_admin)])
def get_config() -> dict:
    v = oidc.get_sso()
    return {"mode": v.get("mode") or "off",
            "protocol": oidc.protocol(v),
            "issuer": v.get("issuer") or "",
            "client_id": v.get("client_id") or "",
            "has_secret": bool(v.get("client_secret_enc")),
            "scopes": v.get("scopes") or "openid profile email",
            "button_label": v.get("button_label") or "Sign in with SSO",
            "jit_default_role": oidc.jit_role(v),
            "configured": oidc.configured(v),
            "effective_mode": oidc.mode(v),
            "licensed": oidc.licensed(),
            "jit_roles": list(oidc.JIT_ROLES),
            "saml_metadata_url": v.get("saml_metadata_url") or "",
            "saml_entity_id": v.get("saml_entity_id") or "",
            "saml_sso_url": v.get("saml_sso_url") or "",
            "saml_cert_loaded": bool(v.get("saml_cert_pem"))}


@router.put("/sso", dependencies=[Depends(require_admin)])
def put_config(body: SsoIn, request: Request) -> dict:
    if not oidc.licensed():
        raise HTTPException(402, "single sign-on requires a Business or MSP "
                                 "license")
    if body.mode not in oidc.MODES:
        raise HTTPException(422, "mode must be off, optional, or required")
    if body.protocol not in ("oidc", "saml"):
        raise HTTPException(422, "protocol must be oidc or saml")
    v = dict(oidc.get_sso())
    v.update({"mode": body.mode, "protocol": body.protocol,
              "issuer": (body.issuer or "").strip().rstrip("/"),
              "client_id": (body.client_id or "").strip(),
              "scopes": (body.scopes or "").strip() or "openid profile email",
              "button_label": (body.button_label or "").strip()
                              or "Sign in with SSO"})
    role = (body.jit_default_role or "user").strip()
    if role not in oidc.JIT_ROLES:
        raise HTTPException(422, "the default role for new SSO users must be "
                                 "one of: " + ", ".join(oidc.JIT_ROLES))
    v["jit_default_role"] = role
    if body.client_secret:
        v["client_secret_enc"] = crypto.encrypt(
            body.client_secret.strip().encode(), crypto._master_key()).hex()
    # SAML: the metadata URL is the single input. Entity id, SSO endpoint and
    # signing certificate are parsed from it AT SAVE TIME and stored, so a
    # sign-in never depends on the IdP's metadata endpoint being reachable.
    # Re-saving re-fetches, which is also how a rotated certificate is picked up.
    old_url = v.get("saml_metadata_url") or ""
    new_url = (body.saml_metadata_url or "").strip()
    v["saml_metadata_url"] = new_url
    if body.protocol == "saml" and new_url:
        if new_url != old_url or not v.get("saml_cert_pem"):
            from app.core import saml
            try:
                meta = saml.fetch_metadata(new_url)
            except Exception as e:
                raise HTTPException(422, f"could not read the IdP metadata: {e}")
            v["saml_entity_id"] = meta["entity_id"]
            v["saml_sso_url"] = meta["sso_url"]
            v["saml_cert_pem"] = meta["cert_pem"]
    if body.mode != "off" and not oidc.configured(v):
        raise HTTPException(422, "fill in the SAML metadata URL before "
                            "enabling single sign-on" if body.protocol == "saml"
                            else "fill in the issuer, client id and client "
                                 "secret before enabling single sign-on")
    if body.mode == "required":
        # The never-locked-out guardrail: an active break-glass admin WITH MFA
        # must exist before password sign-in can be switched off.
        with SessionLocal() as db:
            ok = [u for u in db.query(User).all()
                  if u.role == "admin" and u.is_active is not False
                  and u.breakglass and u.mfa_enabled]
        if not ok:
            raise HTTPException(422, "before requiring SSO, flag at least one "
                                     "active admin as break-glass AND enable "
                                     "MFA on that account - it is the way back "
                                     "in if the identity provider is down")
    oidc.save_sso(v)
    with SessionLocal() as db:
        db.add(AuditLog(actor=request.state.user["username"],
                        action="sso.config", detail={"mode": body.mode,
                                        "protocol": body.protocol}))
        db.commit()
    return get_config()


@router.get("/scim", dependencies=[Depends(require_admin)])
def scim_config(request: Request) -> dict:
    from app.api import routes_scim as scim
    cfg = scim.get_cfg()
    return {"enabled": bool(cfg.get("enabled")),
            "has_token": bool(cfg.get("token_sha256")),
            "licensed": oidc.licensed(),
            "base_url": deploy.public_base(request).rstrip("/") + "/api/scim/v2"}


class ScimIn(BaseModel):
    enabled: bool = False


@router.put("/scim", dependencies=[Depends(require_admin)])
def scim_save(body: ScimIn, request: Request) -> dict:
    from app.api import routes_scim as scim
    if not oidc.licensed():
        raise HTTPException(402, "SCIM provisioning requires a Business or "
                                 "MSP license")
    cfg = dict(scim.get_cfg())
    if body.enabled and not cfg.get("token_sha256"):
        raise HTTPException(422, "generate a bearer token before enabling "
                                 "SCIM provisioning")
    cfg["enabled"] = body.enabled
    scim.save_cfg(cfg)
    with SessionLocal() as db:
        db.add(AuditLog(actor=request.state.user["username"],
                        action="scim.config", detail={"enabled": body.enabled}))
        db.commit()
    return scim_config(request)


@router.post("/scim/token", dependencies=[Depends(require_admin)])
def scim_token(request: Request) -> dict:
    """Mint (or rotate) the SCIM bearer token. Returned ONCE - only its
    sha256 is stored, so it can never be shown again."""
    from app.api import routes_scim as scim
    if not oidc.licensed():
        raise HTTPException(402, "SCIM provisioning requires a Business or "
                                 "MSP license")
    plain, digest = scim.new_token()
    cfg = dict(scim.get_cfg())
    rotated = bool(cfg.get("token_sha256"))
    cfg["token_sha256"] = digest
    scim.save_cfg(cfg)
    with SessionLocal() as db:
        db.add(AuditLog(actor=request.state.user["username"],
                        action="scim.token_rotate" if rotated
                        else "scim.token_create", detail={}))
        db.commit()
    return {"token": plain, "rotated": rotated}


@router.post("/sso/test", dependencies=[Depends(require_admin)])
def test_connection() -> dict:
    """Re-fetch discovery so an admin gets a real answer before enabling."""
    v = oidc.get_sso()
    if oidc.protocol(v) == "saml":
        if not (v.get("saml_metadata_url") or "").strip():
            raise HTTPException(422, "enter the IdP metadata URL first")
        from app.core import saml
        try:
            meta = saml.fetch_metadata(v["saml_metadata_url"])
        except Exception as e:
            raise HTTPException(502, f"could not read the IdP metadata: {e}")
        return {"ok": True, "protocol": "saml",
                "issuer": meta["entity_id"], "sso_url": meta["sso_url"],
                "userinfo": False}
    if not (v.get("issuer") or "").strip():
        raise HTTPException(422, "enter the issuer URL first")
    try:
        doc = oidc.discovery(v["issuer"], force=True)
    except Exception as e:
        raise HTTPException(502, f"could not read the discovery document: {e}")
    return {"ok": True, "issuer": doc.get("issuer"),
            "authorization_endpoint": doc.get("authorization_endpoint"),
            "userinfo": bool(doc.get("userinfo_endpoint"))}


# ---------- push groups (IdP-owned, read-only except the role mapping) ------

@router.get("/push-groups", dependencies=[Depends(require_admin)])
def list_push_groups() -> list[dict]:
    from app.models.db import PushGroup, PushGroupMember
    with SessionLocal() as db:
        out = []
        for g in db.query(PushGroup).order_by(PushGroup.display_name).all():
            n = (db.query(PushGroupMember)
                 .filter(PushGroupMember.group_id == g.id).count())
            out.append({"id": g.id, "display_name": g.display_name,
                        "scim_role": g.scim_role, "members": n,
                        "scim": bool(g.scim_external_id)})
        return out


class GroupRoleIn(BaseModel):
    role: str | None = None


@router.post("/push-groups/{group_id}/role",
             dependencies=[Depends(require_admin)])
def set_group_role(group_id: int, body: GroupRoleIn, request: Request) -> dict:
    """The ONE thing an admin may change about a push group. Membership and
    name belong to the directory - see the 409 below."""
    from app.api.routes_scim import _ROLE_RANK, recompute_scim_roles
    from app.models.db import PushGroup, PushGroupMember
    role = (body.role or "").strip() or None
    if role is not None and role not in _ROLE_RANK:
        raise HTTPException(422, "role must be one of: "
                            + ", ".join(_ROLE_RANK) + ", or empty for no mapping")
    with SessionLocal() as db:
        g = db.get(PushGroup, group_id)
        if g is None:
            raise HTTPException(404, "group not found")
        if not g.scim_external_id:
            raise HTTPException(409, "this group is not managed by your "
                                     "identity provider")
        g.scim_role = role
        # Apply immediately - an admin changing a mapping should not have to
        # wait for the next directory sync to see it take effect.
        members = [m.user_id for m in db.query(PushGroupMember)
                   .filter(PushGroupMember.group_id == g.id)]
        db.flush()
        recompute_scim_roles(db, members)
        db.add(AuditLog(actor=request.state.user["username"],
                        action="scim.group_role",
                        detail={"group": g.display_name, "role": role}))
        db.commit()
    return {"ok": True}
