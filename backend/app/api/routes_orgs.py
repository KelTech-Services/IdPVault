"""MSP client organizations - light CRM + grouping for tenants and scoped users.
Admin-only management, gated behind the `msp` license feature."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.security import require_admin
from app.models.db import AuditLog, Org, SessionLocal, Tenant, User

router = APIRouter(tags=["orgs"], dependencies=[Depends(require_admin)])


def _require_msp() -> None:
    from app.core import license as lic
    if not lic.has_feature("msp"):
        raise HTTPException(402, "client organizations require an MSP license - "
                                 "manage licensing in Settings")


class OrgIn(BaseModel):
    name: str
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    notes: str = ""
    billing_memo: str = ""
    billing_cadence: str = ""   # monthly | annual | ""
    renewal_date: str = ""      # YYYY-MM-DD or ""


def _validate(body: OrgIn) -> None:
    if not (body.name or "").strip():
        raise HTTPException(422, "org name is required")
    if body.billing_cadence not in ("", "monthly", "annual"):
        raise HTTPException(422, "billing_cadence must be monthly, annual, or empty")
    if body.renewal_date:
        try:
            date.fromisoformat(body.renewal_date)
        except ValueError:
            raise HTTPException(422, "renewal_date must be YYYY-MM-DD")


def _row(db, o: Org) -> dict:
    return {"id": o.id, "name": o.name, "contact_name": o.contact_name,
            "contact_email": o.contact_email, "contact_phone": o.contact_phone,
            "notes": o.notes, "billing_memo": o.billing_memo,
            "billing_cadence": o.billing_cadence, "renewal_date": o.renewal_date,
            "tenant_count": db.query(Tenant).filter(Tenant.org_id == o.id).count(),
            "user_count": db.query(User).filter(User.org_id == o.id).count()}


@router.get("/orgs")
def list_orgs() -> list[dict]:
    _require_msp()
    with SessionLocal() as db:
        return [_row(db, o) for o in db.query(Org).order_by(Org.name).all()]


@router.get("/orgs/renewals")
def upcoming_renewals(days: int = 60) -> list[dict]:
    """Orgs whose renewal_date falls within the next N days (dashboard card)."""
    _require_msp()
    days = max(1, min(days, 365))
    today, horizon = date.today(), date.today() + timedelta(days=days)
    out = []
    with SessionLocal() as db:
        for o in db.query(Org).filter(Org.renewal_date != "").all():
            try:
                d = date.fromisoformat(o.renewal_date)
            except ValueError:
                continue
            if d <= horizon:
                out.append({"id": o.id, "name": o.name, "renewal_date": o.renewal_date,
                            "billing_memo": o.billing_memo, "billing_cadence": o.billing_cadence,
                            "overdue": d < today})
    return sorted(out, key=lambda x: x["renewal_date"])


@router.post("/orgs")
def create_org(body: OrgIn, request: Request) -> dict:
    _require_msp()
    _validate(body)
    with SessionLocal() as db:
        if db.query(Org).filter(Org.name == body.name.strip()).first():
            raise HTTPException(409, "an org with that name already exists")
        o = Org(name=body.name.strip(), contact_name=body.contact_name,
                contact_email=body.contact_email, contact_phone=body.contact_phone,
                notes=body.notes, billing_memo=body.billing_memo,
                billing_cadence=body.billing_cadence, renewal_date=body.renewal_date)
        db.add(o)
        db.add(AuditLog(actor=request.state.user["username"], action="org.create",
                        detail={"name": body.name.strip()}))
        db.commit()
        return _row(db, o)


@router.patch("/orgs/{org_id}")
def update_org(org_id: int, body: OrgIn, request: Request) -> dict:
    _require_msp()
    _validate(body)
    with SessionLocal() as db:
        o = db.get(Org, org_id)
        if o is None:
            raise HTTPException(404, "org not found")
        clash = db.query(Org).filter(Org.name == body.name.strip(), Org.id != org_id).first()
        if clash:
            raise HTTPException(409, "an org with that name already exists")
        o.name = body.name.strip()
        o.contact_name, o.contact_email, o.contact_phone = body.contact_name, body.contact_email, body.contact_phone
        o.notes, o.billing_memo = body.notes, body.billing_memo
        o.billing_cadence, o.renewal_date = body.billing_cadence, body.renewal_date
        db.add(AuditLog(actor=request.state.user["username"], action="org.update",
                        detail={"name": o.name}))
        db.commit()
        return _row(db, o)


@router.delete("/orgs/{org_id}")
def delete_org(org_id: int, request: Request) -> dict:
    """Remove an org. Its tenants and users are NOT deleted: tenants become
    unassigned; org-scoped users keep their role but see nothing until
    reassigned to another org."""
    _require_msp()
    with SessionLocal() as db:
        o = db.get(Org, org_id)
        if o is None:
            raise HTTPException(404, "org not found")
        name = o.name
        for t in db.query(Tenant).filter(Tenant.org_id == org_id).all():
            t.org_id = None
        for u in db.query(User).filter(User.org_id == org_id).all():
            u.org_id = None
        db.delete(o)
        db.add(AuditLog(actor=request.state.user["username"], action="org.delete",
                        detail={"name": name}))
        db.commit()
        return {"deleted": name}
