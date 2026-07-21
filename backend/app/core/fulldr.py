"""Full-DR restore: apply a snapshot's encrypted pg_dump back onto the
tenant's configured Full-DR database.

This REPLACES the target database's contents, so the design is guardrails
first: preflight probes and classifies the target (refusing anything that is
not an Authentik database or empty), a rescue dump of the CURRENT database is
taken before anything is written (skippable only by explicit acknowledgment
for the current-DB-is-broken disaster case), and the apply itself runs inside
a single transaction with ON_ERROR_STOP - any failure rolls back and leaves
the database exactly as it was.

The target is always the tenant's OWN configured Full-DR URL. There is no
free-text database target at restore time: to restore into a fresh instance,
point the tenant's Full-DR URL at it in tenant settings first (which also
runs the save-time URL validation).
"""
import logging

from app.core import crypto, storage
from app.models.db import AuditLog, RestoreRun, SessionLocal, Tenant

log = logging.getLogger(__name__)


def _tenant_db_url(db, tenant_id: int):
    """(tenant, data_key, db_url) or raise with a plain reason."""
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise ValueError("tenant not found")
    if not t.enc_db_url:
        raise ValueError("this tenant has no Full-DR Postgres URL configured")
    data_key = crypto.unwrap_data_key(t.wrapped_data_key)
    return t, data_key, crypto.decrypt(t.enc_db_url, data_key).decode()


def _redact(db_url: str) -> str:
    """host:port/dbname only - never echo credentials."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(db_url)
        return f"{u.hostname}:{u.port or 5432}{u.path}"
    except Exception:
        return "(unparseable)"


def preflight(tenant_id: int, snapshot_ts: str) -> dict:
    """Read-only checks before the modal lets anyone near Apply."""
    with SessionLocal() as db:
        t, data_key, db_url = _tenant_db_url(db, tenant_id)
        if not storage.has_dbdump(t.slug, snapshot_ts):
            raise ValueError("this snapshot has no Full-DR dump")
        from app.core.dbdump import probe_target
        probe = probe_target(db_url)   # raises on unreachable / wrong database
        m = storage.read_manifest(t.slug, snapshot_ts) or {}
        return {"slug": t.slug, "snapshot_ts": snapshot_ts,
                "dump_size": storage.dbdump_size(t.slug, snapshot_ts),
                "dump_excluded_ephemeral": bool(t.db_dump_exclude_events),
                "target": _redact(db_url),
                "target_kind": probe["kind"],         # authentik | empty
                "target_tables": probe["tables"],
                "server_version": probe["version"],
                "snapshot_objects": sum((m.get("counts") or {}).values())}


def run_fulldr_restore(tenant_id: int, snapshot_ts: str, actor: str,
                       note: str | None = None, skip_rescue: bool = False,
                       job_id: int | None = None) -> dict:
    """The apply. Route-level checks (admin, password reauth, typed slug)
    happen before this is enqueued; this function re-verifies the physical
    preconditions and does the work."""
    with SessionLocal() as db:
        t, data_key, db_url = _tenant_db_url(db, tenant_id)
        slug, name = t.slug, t.name
    if not storage.has_dbdump(slug, snapshot_ts):
        raise ValueError("this snapshot has no Full-DR dump")

    from app.core.dbdump import pg_dump, probe_target, psql_restore
    probe = probe_target(db_url)   # re-check at run time, not just modal time

    summary: dict = {"snapshot": snapshot_ts, "target": _redact(db_url),
                     "target_kind_before": probe["kind"]}

    # 1) Rescue dump of the CURRENT database - the undo button for the undo.
    if skip_rescue:
        summary["rescue"] = "skipped by operator"
    else:
        try:
            rescue_path = storage.write_rescue_dump(slug, data_key, pg_dump(db_url))
            summary["rescue"] = "saved"
            summary["rescue_file"] = rescue_path.rsplit("/", 2)[-2] + "/" + \
                rescue_path.rsplit("/", 1)[-1]
        except Exception as re_:
            raise RuntimeError(
                "could not take a rescue dump of the CURRENT database, so "
                "nothing was restored. If the current database is broken and "
                "you accept losing its contents, re-run with 'Skip rescue "
                f"dump' checked. ({str(re_)[:200]})")

    # 2) Decrypt the snapshot dump and apply it atomically with real progress.
    sql = storage.read_dbdump(slug, snapshot_ts, data_key)
    _prog = None
    if job_id is not None:
        from app.core.jobs import set_progress

        def _prog(done, total):
            set_progress(job_id, done, total)
        _prog(0, len(sql))
    result = psql_restore(db_url, sql, progress_cb=_prog)
    summary.update(result)

    manual = ["Restart the Authentik server AND worker containers so they pick "
              "up the restored database.",
              "All sessions were replaced - everyone signs in again.",
              "If the API token IdPVault uses was created AFTER this snapshot, "
              "it no longer exists - recreate it in Authentik and update the "
              "tenant settings."]

    with SessionLocal() as db:
        run = RestoreRun(tenant_id=tenant_id, snapshot_ts=snapshot_ts,
                         mode="fulldr_apply", actor=actor,
                         note=(note or "").strip()[:500] or None,
                         summary=summary,
                         results={"manual_steps": manual})
        db.add(run)
        db.add(AuditLog(actor=actor, action="restore.fulldr_apply",
                        detail={"tenant": slug, "snapshot": snapshot_ts,
                                "bytes": result["bytes"],
                                "rescue": summary["rescue"]}))
        db.commit()
        run_id = run.id

    from app.core.alerts import send_alert
    send_alert("restore_applied", f"Full-DR restore applied - {name}",
               f"The Full-DR database dump from snapshot {snapshot_ts} was "
               f"applied to {summary['target']}. The previous contents were "
               f"replaced (rescue dump: {summary['rescue']}).\n\nDo these now:\n"
               + "\n".join(f"  - {m}" for m in manual)
               + (f"\n\nJustification: {note[:400]}" if note else ""),
               {"Tenant": name, "Snapshot": snapshot_ts,
                "Applied": f"{result['bytes']} bytes in "
                           f"{result['duration_ms'] / 1000:.0f}s",
                "Rescue dump": summary["rescue"]})
    return {"restore_run_id": run_id, "summary": summary, "manual_steps": manual}
