"""APScheduler wiring: one cron job per tenant, plus on-demand runs."""
import logging
import os
import time
from datetime import datetime, timezone

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)
# Backups queue and run one at a time by default so modest hosts are never
# overloaded by same-time schedules; hosts with headroom can raise
# IDPVAULT_BACKUP_WORKERS. Same-tenant runs never overlap regardless
# (APScheduler max_instances=1 per job).
_WORKERS = max(1, int(os.environ.get("IDPVAULT_BACKUP_WORKERS", "1")))
scheduler = BackgroundScheduler(
    timezone="UTC",
    executors={"default": ThreadPoolExecutor(_WORKERS)},
    job_defaults={"coalesce": True, "misfire_grace_time": 3600},
)


def org_timezone() -> str:
    """IANA timezone that tenant cron schedules are interpreted in (Settings ->
    org timezone; default UTC). Snapshot names/storage stay UTC regardless."""
    try:
        from zoneinfo import ZoneInfo

        from app.models.db import SessionLocal, Setting
        with SessionLocal() as db:
            row = db.get(Setting, "general")
            tz = (dict(row.value) if row else {}).get("org_timezone") or "UTC"
        ZoneInfo(tz)  # validate; fall back to UTC on bad values
        return tz
    except Exception:
        return "UTC"


def cron_trigger(expr: str) -> CronTrigger:
    """Cron trigger evaluated in the org timezone (DST-correct)."""
    return CronTrigger.from_crontab(expr, timezone=org_timezone())


def load_tenant_jobs() -> None:
    """(Re-)register cron backup jobs for all tenants with a schedule set.
    Safe to call again after settings changes (replace_existing)."""
    from app.models.db import SessionLocal, Tenant

    scheduler.add_job(health_check, CronTrigger.from_crontab("30 0 * * *"), id="health-check", replace_existing=True)
    from apscheduler.triggers.interval import IntervalTrigger
    from app.core.livestate import sweep as livestate_sweep
    # Live-state sweep shares the serial pool with backups; the TTL check inside
    # keeps actual provider polls at the configured cadence.
    scheduler.add_job(livestate_sweep, IntervalTrigger(minutes=5),
                      id="live-state-sweep", replace_existing=True)
    with SessionLocal() as db:
        for t in db.query(Tenant).filter(Tenant.schedule_cron.isnot(None)).all():
            scheduler.add_job(run_backup, cron_trigger(t.schedule_cron),
                              args=[t.id], id=f"backup-{t.id}", replace_existing=True)
            log.info("scheduled config backup tenant=%s cron=%s tz=%s", t.slug, t.schedule_cron, org_timezone())
        from app.core.identity import run_identity_backup
        for t in db.query(Tenant).filter(Tenant.identity_enabled == True,  # noqa: E712
                                         Tenant.identity_schedule_cron.isnot(None)).all():
            scheduler.add_job(run_identity_backup, cron_trigger(t.identity_schedule_cron),
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
    from app.core import license as lic
    if not lic.is_tenant_entitled(tenant_id):
        log.warning("backup skipped tenant=%s - over the license tenant limit", tenant_id)
        return {"manifest": None, "drift": None, "skipped": "license"}
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
                    size = storage.write_dbdump(t.slug, manifest["timestamp"], data_key,
                                                pg_dump(db_url, exclude_events=bool(t.db_dump_exclude_events)))
                    manifest["db_dump"] = {"status": "ok", "size_encrypted": size}
                except Exception as de:
                    # exception text goes to logs only; API/manifest gets a generic marker
                    manifest["db_dump"] = {"status": "failed",
                                           "error": "pg_dump failed - see server logs"}
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
            from app.core.alerts import alert_backup_completed
            alert_backup_completed(t.name, manifest["timestamp"],
                                   sum(manifest["counts"].values()), drift)
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
