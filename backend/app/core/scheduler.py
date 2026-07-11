"""APScheduler wiring: one cron job per tenant, plus on-demand runs."""
import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


def load_tenant_jobs() -> None:
    """Register cron backup jobs for all tenants with a schedule set."""
    from app.models.db import SessionLocal, Tenant

    scheduler.add_job(health_check, CronTrigger.from_crontab("30 0 * * *"), id="health-check", replace_existing=True)
    with SessionLocal() as db:
        for t in db.query(Tenant).filter(Tenant.schedule_cron.isnot(None)).all():
            scheduler.add_job(run_backup, CronTrigger.from_crontab(t.schedule_cron),
                              args=[t.id], id=f"backup-{t.id}", replace_existing=True)
            log.info("scheduled config backup tenant=%s cron=%s", t.slug, t.schedule_cron)
        from app.core.identity import run_identity_backup
        for t in db.query(Tenant).filter(Tenant.identity_enabled == True,  # noqa: E712
                                         Tenant.identity_schedule_cron.isnot(None)).all():
            scheduler.add_job(run_identity_backup, CronTrigger.from_crontab(t.identity_schedule_cron),
                              args=[t.id], id=f"identity-{t.id}", replace_existing=True)
            log.info("scheduled identity backup tenant=%s cron=%s", t.slug, t.identity_schedule_cron)


def run_backup(tenant_id: int) -> dict:
    """Full backup pass for one tenant. Called by cron or the API.
    Always records a BackupRun row (ok or failed); emits Event rows on drift."""
    from app.core import crypto, storage
    from app.core.diff import diff_exports
    from app.core.events import extract_events
    from app.models.db import BackupRun, SessionLocal, Snapshot, Tenant
    from app.providers import get_adapter

    started = time.monotonic()
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise ValueError(f"tenant {tenant_id} not found")
        try:
            data_key = crypto.unwrap_data_key(t.wrapped_data_key)
            creds = crypto.decrypt(t.enc_credentials, data_key).decode()
            adapter = get_adapter(t.provider, t.base_url, creds)

            export = adapter.export()
            manifest = storage.write_snapshot(t.slug, data_key, export)

            manifest["db_dump"] = None
            if t.enc_db_url:
                try:
                    from app.core.dbdump import pg_dump
                    db_url = crypto.decrypt(t.enc_db_url, data_key).decode()
                    size = storage.write_dbdump(t.slug, manifest["timestamp"], data_key, pg_dump(db_url))
                    manifest["db_dump"] = {"status": "ok", "size_encrypted": size}
                except Exception as de:
                    manifest["db_dump"] = {"status": "failed", "error": str(de)[:300]}
                    log.warning("pg_dump failed tenant=%s: %s", t.slug, de)

            prev = storage.list_snapshots(t.slug)
            drift = None
            if len(prev) >= 2:
                old = storage.read_snapshot(t.slug, prev[-2], data_key)
                drift = diff_exports(old, export) or None

            if drift:
                for ev in extract_events(t.id, manifest["timestamp"], drift):
                    db.add(ev)
            db.add(Snapshot(tenant_id=t.id, ts=manifest["timestamp"],
                            counts=manifest["counts"], size=manifest["size_encrypted"],
                            drift=bool(drift)))
            db.add(BackupRun(tenant_id=t.id, ts=manifest["timestamp"], status="ok",
                             duration_ms=int((time.monotonic() - started) * 1000)))
            if t.retention_keep:
                storage.prune(t.slug, t.retention_keep)
            db.commit()
            log.info("backup done tenant=%s ts=%s drift=%s", t.slug, manifest["timestamp"], bool(drift))
            if drift:
                from app.core.alerts import alert_drift
                alert_drift(t.name, manifest["timestamp"], drift)
            from app.core.alerts import alert_backup_success
            alert_backup_success(t.name, manifest["timestamp"], sum(manifest["counts"].values()))
            return {"manifest": manifest, "drift": drift}
        except Exception as e:
            db.rollback()
            db.add(BackupRun(tenant_id=tenant_id,
                             ts=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                             status="failed", error=str(e)[:490],
                             duration_ms=int((time.monotonic() - started) * 1000)))
            db.commit()
            log.exception("backup failed tenant=%s", tenant_id)
            from app.core.alerts import alert_failure
            alert_failure(t.name, str(e)[:490])
            raise


def health_check() -> None:
    # Daily self-health: alert if a scheduled tenant has no recent successful backup.
    from datetime import datetime, timezone
    from app.core.alerts import alert_stale
    from app.models.db import BackupRun, SessionLocal, Setting, Tenant
    with SessionLocal() as db:
        row = db.get(Setting, "general")
        try:
            max_h = int((row.value.get("stale_backup_hours") if row else None) or 26)
        except (TypeError, ValueError):
            max_h = 26
        now = datetime.now(timezone.utc)
        for t in db.query(Tenant).filter(Tenant.schedule_cron.isnot(None)).all():
            q = db.query(BackupRun).filter(BackupRun.tenant_id == t.id, BackupRun.status == "ok")
            last = q.order_by(BackupRun.id.desc()).first()
            age_h = (now - last.at).total_seconds() / 3600 if last else 1000000.0
            if age_h > max_h:
                log.warning("stale backup tenant=%s age=%.0fh", t.slug, age_h)
                alert_stale(t.name, age_h)
