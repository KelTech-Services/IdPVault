"""License install / status / clear — admin only. The token is validated before
it is stored; an invalid or expired token is rejected outright."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

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
    return info


class LicenseIn(BaseModel):
    token: str


@router.put("/license")
def install_license(body: LicenseIn, request: Request) -> dict:
    data = lic.verify(body.token)
    if not data:
        raise HTTPException(422, "license key is invalid or expired - check that it "
                                 "was pasted completely")
    with SessionLocal() as db:
        row = db.get(Setting, "license")
        if row is None:
            db.add(Setting(key="license", value={"token": body.token.strip()}))
        else:
            row.value = {"token": body.token.strip()}
        db.add(AuditLog(actor=request.state.user["username"], action="license.install",
                        detail={"customer": data.get("customer"), "tier": data.get("tier"),
                                "expires": data.get("expires")}))
        db.commit()
    return get_license()


@router.delete("/license")
def clear_license(request: Request) -> dict:
    with SessionLocal() as db:
        row = db.get(Setting, "license")
        if row is not None:
            db.delete(row)
        db.add(AuditLog(actor=request.state.user["username"], action="license.clear",
                        detail={}))
        db.commit()
    return get_license()
