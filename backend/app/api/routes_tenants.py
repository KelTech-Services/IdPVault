"""Tenant CRUD. Credentials are encrypted immediately on ingest, never stored plain."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core import crypto
from app.core.security import require_admin
from app.providers import identity_supported
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
    db_dump_exclude_events: bool = False  # full-DR: skip Authentik event-log rows
    identity_enabled: bool = False
    identity_schedule_cron: str | None = None
    identity_retention_keep: int = 14
    org_id: int | None = None  # MSP: client org assignment


_SLUG_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_DB_URL_HELP = ("Full-DR URL could not be parsed. Format: "
                "postgresql://user:password@host:5432/dbname - if the password "
                "contains special characters (@ : / # etc.) they must be "
                "URL-encoded, e.g. @ becomes %40.")


def _validate_db_url(u: str) -> None:
    """Reject Full-DR URLs libpq cannot parse, at SAVE time - a bad URL must
    not become a silent nightly dump failure. The classic mistake is a raw
    password with @ / : in it (pg_dump then reads the password as the port)."""
    from urllib.parse import urlsplit
    try:
        s = urlsplit(u)
        if s.scheme not in ("postgresql", "postgres") or not s.hostname:
            raise HTTPException(422, _DB_URL_HELP)
        _ = s.port           # raises ValueError when the port slot is not a number
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(422, _DB_URL_HELP)
    authority = u.split("://", 1)[-1].split("/", 1)[0]
    if authority.count("@") > 1:   # raw @ inside the password
        raise HTTPException(422, _DB_URL_HELP)


@router.post("/tenants", dependencies=[Depends(require_admin)])
def create_tenant(body: TenantIn) -> dict:
    from app.core import license as lic
    if body.provider not in ("authentik", "okta", "auth0"):
        raise HTTPException(422, "provider must be authentik, okta, or auth0")
    if not _SLUG_RE.fullmatch(body.slug or ""):
        raise HTTPException(422, "slug must be 1-64 letters, numbers, hyphens, or underscores")
    if body.identity_enabled and not lic.has_feature("identity"):
        raise HTTPException(402, "identity backup requires a paid license - "
                                 "add one in Settings -> License")
    if body.db_url:
        _validate_db_url(body.db_url)
    data_key = crypto.new_data_key()
    with SessionLocal() as db:
        if not lic.can_add_tenant(db.query(Tenant).count()):
            raise HTTPException(402, "tenant limit reached for your license tier - "
                                     "upgrade in Settings -> License to add more tenants")
        t = Tenant(
            name=body.name, slug=body.slug, provider=body.provider,
            base_url=body.base_url,
            enc_credentials=crypto.encrypt(body.api_token.encode(), data_key),
            wrapped_data_key=crypto.wrap_data_key(data_key),
            schedule_cron=body.schedule_cron, retention_keep=body.retention_keep,
            enc_db_url=crypto.encrypt(body.db_url.encode(), data_key) if body.db_url else None,
            db_dump_exclude_events=body.db_dump_exclude_events,
            identity_enabled=body.identity_enabled,
            identity_schedule_cron=body.identity_schedule_cron,
            identity_retention_keep=body.identity_retention_keep,
            org_id=body.org_id if lic.has_feature("msp") else None,
        )
        db.add(t)
        db.add(AuditLog(action="tenant.create", detail={"slug": body.slug}))
        db.commit()
        return {"id": t.id, "slug": t.slug}


@router.get("/tenants")
def list_tenants(request: Request) -> list[dict]:
    from app.core import license as lic
    from app.core.security import visible_tenant_ids
    from app.models.db import Org
    entitled = lic.entitled_tenant_ids()          # None = all entitled
    with SessionLocal() as db:
        vis = visible_tenant_ids(db, request.state.user)   # None = unrestricted
        org_names = {o.id: o.name for o in db.query(Org).all()}
        return [
            {"id": t.id, "name": t.name, "slug": t.slug, "provider": t.provider,
             "active": entitled is None or t.id in entitled,
             "base_url": t.base_url,
             "schedule_cron": t.schedule_cron, "retention_keep": t.retention_keep,
             "db_dr": bool(t.enc_db_url),
             "db_dump_exclude_events": bool(t.db_dump_exclude_events),
             "supports_identity": identity_supported(t.provider),
             "identity_enabled": t.identity_enabled,
             "identity_schedule_cron": t.identity_schedule_cron,
             "identity_retention_keep": t.identity_retention_keep,
             "org_id": t.org_id, "org_name": org_names.get(t.org_id)}
            for t in db.query(Tenant).all() if vis is None or t.id in vis
        ]


@router.delete("/tenants/{tenant_id}", dependencies=[Depends(require_admin)])
def delete_tenant(tenant_id: int) -> dict:
    """Remove a tenant and everything stored for it. Dependent rows go first
    (the tenant FKs have no cascade - deleting any tenant that had ever run a
    backup 500'd on snapshots_tenant_id_fkey, fixed v1.3.1), then the tenant
    row, one transaction. The on-disk tree goes too: the wrapped data key dies
    with the tenant row, so those files would be undecryptable anyway."""
    from app.core import storage
    from app.core.scheduler import scheduler
    from app.models.db import (BackupRun, Event, IdentitySnapshot, Job,
                               RestoreRun, Snapshot, TenantState)
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        slug = t.slug
        for model in (TenantState, Snapshot, BackupRun, Event,
                      IdentitySnapshot, RestoreRun, Job):
            db.query(model).filter(model.tenant_id == tenant_id).delete()
        db.delete(t)
        db.add(AuditLog(action="tenant.delete", detail={"slug": slug}))
        db.commit()
    for job_id in (f"backup-{tenant_id}", f"identity-{tenant_id}"):
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
    try:
        storage.delete_tenant_tree(slug)
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
    db_dump_exclude_events: bool | None = None
    identity_enabled: bool | None = None
    identity_schedule_cron: str | None = None
    identity_retention_keep: int | None = None
    org_id: int | None = None  # MSP org assignment; global admin only


@router.patch("/tenants/{tenant_id}")
def update_tenant(tenant_id: int, body: TenantUpdate, request: Request) -> dict:
    from apscheduler.triggers.cron import CronTrigger
    from app.core import license as lic
    from app.core.scheduler import scheduler, run_backup
    from app.core.security import require_tenant_write
    if body.identity_enabled and not lic.has_feature("identity"):
        raise HTTPException(402, "identity backup requires a paid license - "
                                 "add one in Settings -> License")

    fields = body.model_dump(exclude_unset=True)
    with SessionLocal() as db:
        require_tenant_write(request, db, tenant_id)     # admin, or org_admin in-org
        if request.state.user.get("role") != "admin":
            fields.pop("org_id", None)                   # org assignment is admin-only
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        if "org_id" in fields:
            t.org_id = fields["org_id"] if lic.has_feature("msp") else None
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
            if fields["db_url"]:
                _validate_db_url(fields["db_url"])
            data_key = crypto.unwrap_data_key(t.wrapped_data_key)
            t.enc_db_url = crypto.encrypt(fields["db_url"].encode(), data_key) if fields["db_url"] else None
        if "db_dump_exclude_events" in fields:
            t.db_dump_exclude_events = bool(fields["db_dump_exclude_events"])
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
                from app.core.scheduler import cron_trigger
                scheduler.add_job(run_identity_backup, cron_trigger(t.identity_schedule_cron),
                                  args=[t.id], id=jid, replace_existing=True)
            else:
                try:
                    scheduler.remove_job(jid)
                except Exception:
                    pass
        if "schedule_cron" in fields:
            t.schedule_cron = fields["schedule_cron"] or None
            if t.schedule_cron:
                from app.core.scheduler import cron_trigger
                scheduler.add_job(run_backup, cron_trigger(t.schedule_cron),
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
