"""APScheduler wiring: one cron job per tenant, plus on-demand runs."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


def load_tenant_jobs() -> None:
    """Register cron backup jobs for all tenants with a schedule set."""
    from app.models.db import SessionLocal, Tenant

    with SessionLocal() as db:
        tenants = db.query(Tenant).filter(Tenant.schedule_cron.isnot(None)).all()
        for t in tenants:
            scheduler.add_job(
                run_backup,
                CronTrigger.from_crontab(t.schedule_cron),
                args=[t.id],
                id=f"backup-{t.id}",
                replace_existing=True,
            )
            log.info("scheduled tenant %s (%s) cron=%s", t.id, t.slug, t.schedule_cron)


def run_backup(tenant_id: int) -> dict:
    """Full backup pass for one tenant. Called by cron or the API."""
    from app.core import crypto, storage
    from app.core.diff import diff_exports
    from app.models.db import SessionLocal, Snapshot, Tenant
    from app.providers import get_adapter

    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise ValueError(f"tenant {tenant_id} not found")
        data_key = crypto.unwrap_data_key(t.wrapped_data_key)
        creds = crypto.decrypt(t.enc_credentials, data_key).decode()
        adapter = get_adapter(t.provider, t.base_url, creds)

        export = adapter.export()
        manifest = storage.write_snapshot(t.slug, data_key, export)

        prev = storage.list_snapshots(t.slug)
        drift = None
        if len(prev) >= 2:
            old = storage.read_snapshot(t.slug, prev[-2], data_key)
            drift = diff_exports(old, export) or None

        db.add(Snapshot(tenant_id=t.id, ts=manifest["timestamp"],
                        counts=manifest["counts"], size=manifest["size_encrypted"],
                        drift=bool(drift)))
        if t.retention_keep:
            storage.prune(t.slug, t.retention_keep)
        db.commit()
        log.info("backup done tenant=%s ts=%s drift=%s", t.slug, manifest["timestamp"], bool(drift))
        return {"manifest": manifest, "drift": drift}
