"""SCIM 2.0 provisioning at /api/scim/v2 (ported from StackMerger v0.6.1).

Protocol-independent: SCIM works alongside OIDC or SAML, or on its own.

Two rules that are not negotiable:
- DEPROVISION MEANS DISABLE. A SCIM delete or active=false disables the
  account; it never deletes rows. Removing someone from the directory must
  not destroy their audit trail.
- SCIM NEVER TOUCHES MSP SCOPING. Push groups are the customer's own staff
  directory groups; orgs are their client companies. Group role mapping skips
  org-scoped users entirely.
"""
import hashlib
import hmac
import secrets as pysecrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func

from app.core import oidc
from app.core import security as sec
from app.models.db import (AuditLog, PushGroup, PushGroupMember, SessionLocal,
                           Setting, User)

router = APIRouter(tags=["scim"])

TOKEN_PREFIX = "idpv_scim_"
SCIM_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_RESP = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

# Global roles only. org_admin/org_viewer are MSP client roles that require an
# org_id the directory has no way to supply - they stay hand-assigned.
_ROLE_RANK = {"user": 0, "admin": 1}


def _now():
    return datetime.now(timezone.utc)


def _audit(db, action, detail):
    db.add(AuditLog(actor="scim", action=action, detail=detail))


# ---------- config + bearer token ----------

def get_cfg() -> dict:
    with SessionLocal() as db:
        row = db.get(Setting, "scim")
        return dict(row.value) if row else {}


def save_cfg(v: dict) -> None:
    with SessionLocal() as db:
        row = db.get(Setting, "scim")
        if row is None:
            db.add(Setting(key="scim", value=v))
        else:
            row.value = v
        db.commit()


def new_token() -> tuple[str, str]:
    """Returns (plaintext, sha256). The plaintext is shown ONCE."""
    tok = TOKEN_PREFIX + pysecrets.token_urlsafe(32)
    return tok, hashlib.sha256(tok.encode()).hexdigest()


def _require_token(request: Request) -> None:
    """Bearer auth with a constant-time compare. SCIM has no session."""
    if not oidc.licensed():
        raise HTTPException(404, "not found")
    cfg = get_cfg()
    want = cfg.get("token_sha256") or ""
    if not cfg.get("enabled") or not want:
        raise HTTPException(404, "not found")
    hdr = request.headers.get("authorization", "")
    if not hdr.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    got = hashlib.sha256(hdr.split(None, 1)[1].strip().encode()).hexdigest()
    if not hmac.compare_digest(got, want):
        raise HTTPException(401, "invalid bearer token")


def _err(status: int, detail: str):
    return JSONResponse(status_code=status, content={
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "detail": detail, "status": str(status)})


# ---------- representations ----------

def _user_json(u: User) -> dict:
    return {
        "schemas": [SCIM_USER],
        "id": str(u.id),
        "externalId": u.scim_external_id,
        "userName": u.email or u.username,
        "name": {"givenName": u.first_name or "",
                 "familyName": u.last_name or "",
                 "formatted": sec.display_name(u)},
        "displayName": sec.display_name(u),
        "emails": ([{"value": u.email, "primary": True}] if u.email else []),
        "active": bool(u.is_active),
        "meta": {"resourceType": "User"},
    }


def _group_json(db, g: PushGroup) -> dict:
    members = (db.query(User)
               .join(PushGroupMember, PushGroupMember.user_id == User.id)
               .filter(PushGroupMember.group_id == g.id).all())
    return {
        "schemas": [SCIM_GROUP],
        "id": str(g.id),
        "externalId": g.scim_external_id,
        "displayName": g.display_name,
        "members": [{"value": str(m.id), "display": sec.display_name(m)}
                    for m in members],
        "meta": {"resourceType": "Group"},
    }


def _list(resources: list) -> dict:
    return {"schemas": [LIST_RESP], "totalResults": len(resources),
            "startIndex": 1, "itemsPerPage": len(resources),
            "Resources": resources}


def _eq_filter(flt: str) -> str | None:
    """Minimal 'attr eq "value"' support - the only filter Okta/Authentik/
    Entra actually send for provisioning."""
    if not flt or " eq " not in flt:
        return None
    val = flt.split(" eq ", 1)[1].strip()
    return val.strip('"').strip("'")


# ---------- ServiceProviderConfig ----------

@router.get("/scim/v2/ServiceProviderConfig")
def spc(request: Request):
    _require_token(request)
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:"
                        "ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0,
                     "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": 500},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {"type": "oauthbearertoken", "name": "OAuth Bearer Token",
                 "description": "Bearer token issued in IdPVault settings"}]}


# ---------- Users ----------

@router.get("/scim/v2/Users")
def list_users(request: Request, filter: str = "", count: int = 200):
    _require_token(request)
    with SessionLocal() as db:
        q = db.query(User)
        want = _eq_filter(filter)
        if want:
            q = q.filter(func.lower(User.email) == want.strip().lower())
        return _list([_user_json(u) for u in q.limit(max(1, count)).all()])


@router.get("/scim/v2/Users/{user_id}")
def get_user(user_id: int, request: Request):
    _require_token(request)
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            return _err(404, "user not found")
        return _user_json(u)


@router.post("/scim/v2/Users")
async def create_user(request: Request):
    _require_token(request)
    body = await request.json()
    email = sec.normalize_email(
        (body.get("userName") or "")
        or ((body.get("emails") or [{}])[0].get("value") or ""))
    if not email:
        return _err(400, "userName (an email address) is required")
    name = body.get("name") or {}
    first = (name.get("givenName") or "").strip() or None
    last = (name.get("familyName") or "").strip() or None
    with SessionLocal() as db:
        # Idempotent: IdPs retry creates, and a duplicate must return the
        # existing user rather than a second account or a 500.
        u = (db.query(User)
             .filter(func.lower(User.email) == email, User.email != "")
             .first())
        created = False
        if u is None:
            u = User(username=sec.username_from_email(db, email),
                     email=email,
                     password_hash=sec.hash_password(pysecrets.token_urlsafe(32)),
                     role=oidc.jit_role(),
                     org_id=None,          # SCIM users are never MSP-scoped
                     sso_user=True,
                     is_active=bool(body.get("active", True)),
                     first_name=first, last_name=last,
                     scim_external_id=body.get("externalId"),
                     created_at=_now())
            db.add(u)
            created = True
        else:
            if first:
                u.first_name = first
            if last:
                u.last_name = last
            if body.get("externalId"):
                u.scim_external_id = body["externalId"]
        _audit(db, "scim.user_create" if created else "scim.user_exists",
               {"email": email})
        db.commit()
        db.refresh(u)
        payload = _user_json(u)
    return JSONResponse(status_code=201 if created else 200, content=payload)


def _apply_user(db, u: User, body: dict) -> None:
    name = body.get("name") or {}
    if name.get("givenName") is not None:
        u.first_name = (name.get("givenName") or "").strip() or None
    if name.get("familyName") is not None:
        u.last_name = (name.get("familyName") or "").strip() or None
    if body.get("externalId"):
        u.scim_external_id = body["externalId"]
    if "active" in body:
        _set_active(db, u, bool(body["active"]))


def _set_active(db, u: User, active: bool) -> None:
    """Deprovision = DISABLE. Also refuses to disable the last active admin,
    which would leave the install unmanageable from a directory mistake."""
    if u.is_active == active:
        return
    if not active and u.role == "admin" and not db.query(User).filter(
            User.role == "admin", User.id != u.id,
            User.is_active.is_(True)).count():
        _audit(db, "scim.deactivate_skipped",
               {"username": u.username, "reason": "last active administrator"})
        return
    # A directory mistake must never disable the last break-glass admin while
    # SSO is required - that is the only way back in.
    if not active and u.breakglass and oidc.mode() == "required" \
            and not db.query(User).filter(
                User.role == "admin", User.id != u.id,
                User.is_active.is_(True), User.breakglass.is_(True),
                User.mfa_enabled.is_(True)).count():
        _audit(db, "scim.deactivate_skipped",
               {"username": u.username,
                "reason": "last break-glass admin while SSO is required"})
        return
    u.is_active = active
    _audit(db, "scim.user_active", {"username": u.username, "active": active})


@router.put("/scim/v2/Users/{user_id}")
async def put_user(user_id: int, request: Request):
    _require_token(request)
    body = await request.json()
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            return _err(404, "user not found")
        _apply_user(db, u, body)
        db.commit()
        db.refresh(u)
        return _user_json(u)


@router.patch("/scim/v2/Users/{user_id}")
async def patch_user(user_id: int, request: Request):
    _require_token(request)
    body = await request.json()
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            return _err(404, "user not found")
        for op in (body.get("Operations") or []):
            path = (op.get("path") or "").strip()
            val = op.get("value")
            if isinstance(val, dict) and not path:
                _apply_user(db, u, val)
                continue
            if path == "active":
                _set_active(db, u, bool(val))
            elif path == "name.givenName":
                u.first_name = (str(val).strip() or None) if val else None
            elif path == "name.familyName":
                u.last_name = (str(val).strip() or None) if val else None
            elif path == "externalId" and val:
                u.scim_external_id = str(val)
        db.commit()
        db.refresh(u)
        return _user_json(u)


@router.delete("/scim/v2/Users/{user_id}")
def delete_user(user_id: int, request: Request):
    """SCIM delete = DISABLE, never a row delete. History survives."""
    _require_token(request)
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None:
            return _err(404, "user not found")
        _set_active(db, u, False)
        db.commit()
    return JSONResponse(status_code=204, content=None)


# ---------- role mapping ----------
# A push group can carry a role; a member gets the HIGHEST mapped role across
# all their push groups. Recomputed on EVERY membership change - leaving all
# mapped groups drops the user to the SSO default role, so the directory
# drives the whole lifecycle.

def recompute_scim_roles(db, user_ids) -> None:
    default_role = oidc.jit_role()
    for uid in set(user_ids):
        u = db.get(User, uid)
        if u is None:
            continue
        rows = (db.query(PushGroup.scim_role)
                .join(PushGroupMember,
                      PushGroupMember.group_id == PushGroup.id)
                .filter(PushGroupMember.user_id == uid,
                        PushGroup.scim_external_id.isnot(None),
                        PushGroup.scim_role.isnot(None)).all())
        mapped = [r for (r,) in rows if r in _ROLE_RANK]
        new_role = (max(mapped, key=lambda r: _ROLE_RANK[r]) if mapped
                    else default_role)
        if new_role == u.role:
            continue
        # Brick-proof. Every skip is audited so a confused admin can see WHY
        # the directory did not win.
        if u.breakglass:
            _audit(db, "scim.role_skipped",
                   {"username": u.username, "reason": "break-glass account"})
            continue
        if u.org_id or u.role in ("org_admin", "org_viewer"):
            # MSP client users are not staff; the directory does not own them.
            _audit(db, "scim.role_skipped",
                   {"username": u.username, "reason": "org-scoped user"})
            continue
        if u.role == "admin" and new_role != "admin" and not db.query(
                User).filter(User.role == "admin", User.id != u.id,
                             User.is_active.is_(True)).count():
            _audit(db, "scim.role_skipped",
                   {"username": u.username,
                    "reason": "last active administrator"})
            continue
        u.role = new_role
        _audit(db, "user.role", {"username": u.username, "role": new_role,
                                 "via": "SCIM group mapping"})


def _sync_members(db, g: PushGroup, want_ids: set) -> set:
    have = {m.user_id for m in db.query(PushGroupMember)
            .filter(PushGroupMember.group_id == g.id)}
    affected = set()
    for uid in want_ids - have:
        if db.get(User, uid) is None:
            continue
        db.add(PushGroupMember(group_id=g.id, user_id=uid, created_at=_now()))
        affected.add(uid)
    if have - want_ids:
        db.query(PushGroupMember).filter(
            PushGroupMember.group_id == g.id,
            PushGroupMember.user_id.in_(have - want_ids)).delete(
            synchronize_session=False)
        affected |= (have - want_ids)
    return affected


def _member_ids(body: dict) -> set:
    out = set()
    for m in (body.get("members") or []):
        try:
            out.add(int(m.get("value")))
        except (TypeError, ValueError):
            continue
    return out


# ---------- Groups ----------

@router.get("/scim/v2/Groups")
def list_groups(request: Request, filter: str = "", count: int = 200):
    _require_token(request)
    with SessionLocal() as db:
        q = db.query(PushGroup)
        want = _eq_filter(filter)
        if want:
            q = q.filter(PushGroup.display_name == want)
        return _list([_group_json(db, g)
                      for g in q.limit(max(1, count)).all()])


@router.get("/scim/v2/Groups/{group_id}")
def get_group(group_id: int, request: Request):
    _require_token(request)
    with SessionLocal() as db:
        g = db.get(PushGroup, group_id)
        if g is None:
            return _err(404, "group not found")
        return _group_json(db, g)


@router.post("/scim/v2/Groups")
async def create_group(request: Request):
    _require_token(request)
    body = await request.json()
    name = (body.get("displayName") or "").strip()
    if not name:
        return _err(400, "displayName is required")
    with SessionLocal() as db:
        ext = body.get("externalId")
        g = None
        if ext:
            g = db.query(PushGroup).filter(
                PushGroup.scim_external_id == ext).first()
        if g is None:
            g = db.query(PushGroup).filter(
                PushGroup.display_name == name).first()
        created = False
        if g is None:
            g = PushGroup(display_name=name, scim_external_id=ext,
                          created_at=_now())
            db.add(g)
            db.flush()
            created = True
        else:
            g.display_name = name
            if ext:
                g.scim_external_id = ext
        affected = _sync_members(db, g, _member_ids(body))
        db.flush()
        recompute_scim_roles(db, affected)
        _audit(db, "scim.group_create" if created else "scim.group_update",
               {"group": name})
        db.commit()
        db.refresh(g)
        payload = _group_json(db, g)
    return JSONResponse(status_code=201 if created else 200, content=payload)


@router.put("/scim/v2/Groups/{group_id}")
async def put_group(group_id: int, request: Request):
    _require_token(request)
    body = await request.json()
    with SessionLocal() as db:
        g = db.get(PushGroup, group_id)
        if g is None:
            return _err(404, "group not found")
        if (body.get("displayName") or "").strip():
            g.display_name = body["displayName"].strip()
        affected = _sync_members(db, g, _member_ids(body))
        db.flush()
        recompute_scim_roles(db, affected)
        _audit(db, "scim.group_update", {"group": g.display_name})
        db.commit()
        db.refresh(g)
        return _group_json(db, g)


@router.patch("/scim/v2/Groups/{group_id}")
async def patch_group(group_id: int, request: Request):
    """Member add/remove. NOTE: these operations run the SAME role recompute
    as PUT - StackMerger originally had a bypass here where PATCH member-adds
    skipped the guards, which is exactly how a last-admin demotion sneaks in."""
    _require_token(request)
    body = await request.json()
    with SessionLocal() as db:
        g = db.get(PushGroup, group_id)
        if g is None:
            return _err(404, "group not found")
        have = {m.user_id for m in db.query(PushGroupMember)
                .filter(PushGroupMember.group_id == g.id)}
        want = set(have)
        for op in (body.get("Operations") or []):
            kind = (op.get("op") or "").lower()
            path = (op.get("path") or "").strip()
            val = op.get("value")
            if path.startswith("members"):
                ids = set()
                if isinstance(val, list):
                    ids = _member_ids({"members": val})
                elif isinstance(val, dict):
                    ids = _member_ids({"members": [val]})
                elif val is not None:
                    try:
                        ids = {int(val)}
                    except (TypeError, ValueError):
                        ids = set()
                if kind == "add":
                    want |= ids
                elif kind == "remove":
                    want -= (ids or have)
                elif kind == "replace":
                    want = ids
            elif path == "displayName" and val:
                g.display_name = str(val).strip()
            elif not path and isinstance(val, dict):
                if val.get("displayName"):
                    g.display_name = str(val["displayName"]).strip()
                if "members" in val:
                    want = _member_ids(val)
        affected = _sync_members(db, g, want)
        db.flush()
        recompute_scim_roles(db, affected)
        _audit(db, "scim.group_update", {"group": g.display_name})
        db.commit()
        db.refresh(g)
        return _group_json(db, g)


@router.delete("/scim/v2/Groups/{group_id}")
def delete_group(group_id: int, request: Request):
    """A group really is removed - it is directory metadata, not a person.
    Members drop to the default role through the usual recompute."""
    _require_token(request)
    with SessionLocal() as db:
        g = db.get(PushGroup, group_id)
        if g is None:
            return _err(404, "group not found")
        affected = {m.user_id for m in db.query(PushGroupMember)
                    .filter(PushGroupMember.group_id == g.id)}
        db.query(PushGroupMember).filter(
            PushGroupMember.group_id == g.id).delete(
            synchronize_session=False)
        name = g.display_name
        db.delete(g)
        db.flush()
        recompute_scim_roles(db, affected)
        _audit(db, "scim.group_delete", {"group": name})
        db.commit()
    return JSONResponse(status_code=204, content=None)
