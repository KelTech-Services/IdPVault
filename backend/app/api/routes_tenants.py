"""Tenant CRUD. Credentials are encrypted immediately on ingest, never stored plain."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import crypto
from app.core.security import require_admin
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
    db_url: str | None = None  # optional: full-DR pg_dump source (self-hosted only)
    identity_enabled: bool = False
    identity_schedule_cron: str | None = None
    identity_retention_keep: int = 14


@router.post("/tenants", dependencies=[Depends(require_admin)])
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
            enc_db_url=crypto.encrypt(body.db_url.encode(), data_key) if body.db_url else None,
            identity_enabled=body.identity_enabled,
            identity_schedule_cron=body.identity_schedule_cron,
            identity_retention_keep=body.identity_retention_keep,
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
             "base_url": t.base_url,
             "schedule_cron": t.schedule_cron, "retention_keep": t.retention_keep,
             "db_dr": bool(t.enc_db_url),
             "identity_enabled": t.identity_enabled,
             "identity_schedule_cron": t.identity_schedule_cron,
             "identity_retention_keep": t.identity_retention_keep}
            for t in db.query(Tenant).all()
        ]


@router.delete("/tenants/{tenant_id}", dependencies=[Depends(require_admin)])
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


class TenantUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_token: str | None = None       # provide to rotate; omit to keep
    schedule_cron: str | None = None   # explicit null/empty clears the schedule
    retention_keep: int | None = None
    db_url: str | None = None          # set to configure full-DR; "" clears it
    identity_enabled: bool | None = None
    identity_schedule_cron: str | None = None
    identity_retention_keep: int | None = None


@router.patch("/tenants/{tenant_id}", dependencies=[Depends(require_admin)])
def update_tenant(tenant_id: int, body: TenantUpdate) -> dict:
    from apscheduler.triggers.cron import CronTrigger
    from app.core.scheduler import scheduler, run_backup

    fields = body.model_dump(exclude_unset=True)
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        if "name" in fields and fields["name"]:
            t.name = fields["name"]
        if "base_url" in fields and fields["base_url"]:
            t.base_url = fields["base_url"]
        if "retention_keep" in fields and fields["retention_keep"]:
            t.retention_keep = fields["retention_keep"]
        if fields.get("api_token"):
            data_key = crypto.unwrap_data_key(t.wrapped_data_key)
            t.enc_credentials = crypto.encrypt(fields["api_token"].encode(), data_key)
        if "db_url" in fields:
            data_key = crypto.unwrap_data_key(t.wrapped_data_key)
            t.enc_db_url = crypto.encrypt(fields["db_url"].encode(), data_key) if fields["db_url"] else None
        if "identity_enabled" in fields:
            t.identity_enabled = bool(fields["identity_enabled"])
        if "identity_retention_keep" in fields and fields["identity_retention_keep"]:
            t.identity_retention_keep = fields["identity_retention_keep"]
        if "identity_schedule_cron" in fields:
            from apscheduler.triggers.cron import CronTrigger
            from app.core.scheduler import scheduler
            from app.core.identity import run_identity_backup
            t.identity_schedule_cron = fields["identity_schedule_cron"] or None
            jid = f"identity-{t.id}"
            if t.identity_enabled and t.identity_schedule_cron:
                scheduler.add_job(run_identity_backup, CronTrigger.from_crontab(t.identity_schedule_cron),
                                  args=[t.id], id=jid, replace_existing=True)
            else:
                try:
                    scheduler.remove_job(jid)
                except Exception:
                    pass
        if "schedule_cron" in fields:
            t.schedule_cron = fields["schedule_cron"] or None
            if t.schedule_cron:
                scheduler.add_job(run_backup, CronTrigger.from_crontab(t.schedule_cron),
                                  args=[t.id], id=f"backup-{t.id}", replace_existing=True)
            else:
                try:
                    scheduler.remove_job(f"backup-{t.id}")
                except Exception:
                    pass
        db.add(AuditLog(action="tenant.update",
                        detail={"slug": t.slug, "fields": [k for k in fields if k != "api_token"],
                                "token_rotated": bool(fields.get("api_token"))}))
        db.commit()
        return {"id": t.id, "slug": t.slug}
