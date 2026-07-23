"""License install / status / clear — admin only. One input handles all three
shapes: an IDPV activation key (activated against the license server), an
offline entitlement file's contents, or a legacy full key (both verified
offline). Whatever is stored is validated before it is stored."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core import activation as act
from app.core import license as lic
from app.core.security import require_admin
from app.models.db import AuditLog, SessionLocal, Setting, Tenant, User

router = APIRouter(tags=["license"], dependencies=[Depends(require_admin)])


@router.get("/license")
def get_license() -> dict:
    info = lic.current_license()
    with SessionLocal() as db:
        tenant_count = db.query(Tenant).count()
        info["user_count"] = db.query(User).count()
    ids = lic.entitled_tenant_ids()
    info["tenant_count"] = tenant_count
    info["entitled_tenant_ids"] = sorted(ids) if ids is not None else None
    info["grace_days"] = lic.GRACE_DAYS
    info["instance_id"] = act.instance_id()
    return info


class LicenseIn(BaseModel):
    token: str


@router.put("/license")
def install_license(body: LicenseIn, request: Request) -> dict:
    raw = body.token.strip()
    if act.is_activation_key(raw):
        # Activation key: trade it for an instance-bound entitlement.
        try:
            resp = act.activate(raw)
        except act.ActivationError as e:
            raise HTTPException(e.status if e.status in (403, 404, 409) else 422,
                                str(e))
        except act.ServerUnreachable:
            raise HTTPException(502, "could not reach the license server "
                                     "(license.keltech.ai) - check outbound "
                                     "network access, or use an offline license "
                                     "file from the customer portal")
        detail = {"kind": "activation", "tier": resp.get("tier"),
                  "expires": resp.get("paid_through")}
        _audit(request, "license.install", detail)
        return get_license()
    # Offline entitlement file or legacy full key: verified offline.
    data = lic.verify(raw)
    if not data:
        p = lic.peek(raw)
        if p and p.get("kind") == "entitlement" and \
                p.get("instance_id") != act.instance_id():
            raise HTTPException(422, "this offline license file was issued for a "
                                     "different install - request one for THIS "
                                     "install id (shown below) from the portal")
        raise HTTPException(422, "license key is invalid or expired - check that it "
                                 "was pasted completely")
    kind = "offline" if data.get("kind") == "entitlement" else "legacy"
    with SessionLocal() as db:
        row = db.get(Setting, "license")
        value = {"token": raw, "kind": kind}
        if row is None:
            db.add(Setting(key="license", value=value))
        else:
            row.value = value
        db.add(AuditLog(actor=request.state.user["username"], action="license.install",
                        detail={"kind": kind, "customer": data.get("customer"),
                                "tier": data.get("tier"),
                                "expires": data.get("expires")}))
        db.commit()
    return get_license()


def _audit(request: Request, action: str, detail: dict) -> None:
    with SessionLocal() as db:
        db.add(AuditLog(actor=request.state.user["username"], action=action,
                        detail=detail))
        db.commit()


@router.delete("/license")
def clear_license(request: Request) -> dict:
    # Activation kind: release the server-side binding first (best effort) so
    # the key is immediately usable on another install.
    cur = act.stored()
    released = None
    if cur.get("kind") == "activation" and cur.get("key"):
        released = act.deactivate_remote(cur["key"])
    with SessionLocal() as db:
        row = db.get(Setting, "license")
        if row is not None:
            db.delete(row)
        db.add(AuditLog(actor=request.state.user["username"], action="license.clear",
                        detail={"kind": cur.get("kind") or "legacy",
                                "server_released": released}))
        db.commit()
    return get_license()
