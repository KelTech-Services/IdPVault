"""Tenant CRUD. Credentials are encrypted immediately on ingest, never stored plain."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import crypto
from app.models.db import AuditLog, SessionLocal, Tenant

router = APIRouter(tags=["tenants"])


class TenantIn(BaseModel):
    name: str
    slug: str
    provider: str  # authentik | okta | auth0
    base_url: str
    api_token: str
    schedule_cron: str | None = None  # e.g. "0 3 * * *"
    retention_keep: int = 30


@router.post("/tenants")
def create_tenant(body: TenantIn) -> dict:
    if body.provider not in ("authentik", "okta", "auth0"):
        raise HTTPException(422, "provider must be authentik, okta, or auth0")
    data_key = crypto.new_data_key()
    with SessionLocal() as db:
        t = Tenant(
            name=body.name, slug=body.slug, provider=body.provider,
            base_url=body.base_url,
            enc_credentials=crypto.encrypt(body.api_token.encode(), data_key),
            wrapped_data_key=crypto.wrap_data_key(data_key),
            schedule_cron=body.schedule_cron, retention_keep=body.retention_keep,
        )
        db.add(t)
        db.add(AuditLog(action="tenant.create", detail={"slug": body.slug}))
        db.commit()
        return {"id": t.id, "slug": t.slug}


@router.get("/tenants")
def list_tenants() -> list[dict]:
    with SessionLocal() as db:
        return [
            {"id": t.id, "name": t.name, "slug": t.slug, "provider": t.provider,
             "schedule_cron": t.schedule_cron, "retention_keep": t.retention_keep}
            for t in db.query(Tenant).all()
        ]


@router.delete("/tenants/{tenant_id}")
def delete_tenant(tenant_id: int) -> dict:
    """Remove a tenant record. Snapshots on disk are intentionally kept."""
    from app.core.scheduler import scheduler
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        slug = t.slug
        db.delete(t)
        db.add(AuditLog(action="tenant.delete", detail={"slug": slug}))
        db.commit()
    try:
        scheduler.remove_job(f"backup-{tenant_id}")
    except Exception:
        pass
    return {"deleted": tenant_id, "slug": slug}
