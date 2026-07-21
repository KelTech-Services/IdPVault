"""Background job queue for backups and identity restores.

Jobs run on the SAME serial APScheduler pool as scheduled backups, preserving
the one-at-a-time guarantee on modest hosts. Every run - API-triggered or
cron - records a Job row; the UI's nav activity area polls /jobs/active to
show queued and running work with progress.

Progress is measured in provider API calls: adapters already count calls
(adapter._rl.calls) and the last successful identity snapshot's api_calls
gives an expected total. A small sampler thread copies the live counter into
the job row every couple of seconds. Runs with no history have no total and
render as indeterminate activity.
"""
import logging
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_SAMPLE_SECS = 2.0


def _now():
    return datetime.now(timezone.utc)


def _insert(kind, tenant_id, requested_by, trigger, params=None) -> int:
    from app.models.db import Job, SessionLocal
    with SessionLocal() as db:
        job = Job(tenant_id=tenant_id, kind=kind, trigger=trigger,
                  status="queued", requested_by=requested_by,
                  detail={"params": params or {}})
        db.add(job)
        db.commit()
        return job.id


def enqueue(kind, tenant_id, requested_by, trigger="manual", params=None) -> int:
    """Record a queued job and hand it to the serial pool; returns job id
    immediately. misfire_grace_time=None: a job stuck behind a long backup
    must still run no matter how late it starts."""
    from app.core.scheduler import scheduler
    jid = _insert(kind, tenant_id, requested_by, trigger, params)
    scheduler.add_job(execute, args=[jid], id=f"job-{jid}",
                      misfire_grace_time=None)
    return jid


def scheduled_config_backup(tenant_id: int) -> None:
    """Cron entrypoint: record the row, execute inline (already on the pool)."""
    execute(_insert("config_backup", tenant_id, "scheduler", "scheduled"))


def scheduled_identity_backup(tenant_id: int) -> None:
    execute(_insert("identity_backup", tenant_id, "scheduler", "scheduled"))


def set_progress(job_id: int, done: int, total=None) -> None:
    from app.models.db import Job, SessionLocal
    with SessionLocal() as db:
        j = db.get(Job, job_id)
        if j is None:
            return
        j.progress_done = int(done)
        if total is not None:
            j.progress_total = int(total)
        db.commit()


def sampler(adapter, job_id, total=None):
    """Copy the adapter's live API-call counter into the job row every
    _SAMPLE_SECS from a daemon thread. Returns a stop() callable. Progress
    reporting must never kill the job - all errors are swallowed."""
    stop_ev = threading.Event()

    def _loop():
        from app.models.db import Job, SessionLocal
        while not stop_ev.wait(_SAMPLE_SECS):
            try:
                with SessionLocal() as db:   # self-stop when the job is done -
                    j = db.get(Job, job_id)  # safety net if stop() is never called
                    if j is None or j.status != "running":
                        return
                calls = getattr(getattr(adapter, "_rl", None), "calls", 0)
                set_progress(job_id, calls, total)
            except Exception:
                log.debug("progress sample failed job=%s", job_id, exc_info=True)

    t = threading.Thread(target=_loop, name=f"job-progress-{job_id}", daemon=True)
    t.start()

    def stop():
        stop_ev.set()
        t.join(timeout=_SAMPLE_SECS + 1)

    return stop


def _finish(job_id, status, result=None, error=None):
    from app.models.db import Job, SessionLocal
    with SessionLocal() as db:
        j = db.get(Job, job_id)
        if j is None:
            return
        j.status = status
        j.finished_at = _now()
        detail = dict(j.detail or {})
        if result is not None:
            detail["result"] = result
        if error:
            detail["error"] = str(error)[:490]
        j.detail = detail
        if status == "ok" and j.progress_total:
            j.progress_done = j.progress_total
        db.commit()


def _trim(kind, result):
    """Job rows keep a small JSON-safe result summary - never full manifests."""
    if not isinstance(result, dict):
        return {}
    if kind == "config_backup":
        m = result.get("manifest") or {}
        return {"timestamp": m.get("timestamp"), "counts": m.get("counts"),
                "drift": bool(result.get("drift")), "skipped": result.get("skipped")}
    if kind == "identity_backup":
        m = result.get("manifest") or {}
        return {"timestamp": m.get("timestamp"), "counts": m.get("counts"),
                "api_calls": result.get("api_calls"),
                "duration_ms": result.get("duration_ms"),
                "skipped": result.get("skipped")}
    if kind == "identity_restore":
        return {"restore_run_id": result.get("restore_run_id"),
                "summary": result.get("summary"),
                "manual_steps": result.get("manual_steps")}
    if kind == "config_restore":
        return {"restore_run_id": result.get("restore_run_id"),
                "summary": result.get("summary")}
    return {}


def execute(job_id: int) -> None:
    from app.models.db import Job, SessionLocal
    with SessionLocal() as db:
        j = db.get(Job, job_id)
        if j is None or j.status != "queued":
            return
        j.status = "running"
        j.started_at = _now()
        db.commit()
        kind, tid, trig = j.kind, j.tenant_id, j.trigger
        params = (dict(j.detail or {}).get("params")) or {}
    try:
        if kind == "config_backup":
            from app.core.scheduler import run_backup
            result = run_backup(tid, trigger=trig, job_id=job_id)
        elif kind == "identity_backup":
            from app.core.identity import run_identity_backup
            result = run_identity_backup(tid, job_id=job_id)
        elif kind == "identity_restore":
            from app.core.identity import apply_identity_restore
            result = apply_identity_restore(tid, params["snapshot_ts"],
                                            params.get("actor", "api"),
                                            params.get("selection"), job_id=job_id,
                                            revert_keys=params.get("revert_selection"),
                                            note=params.get("note"))
        elif kind == "config_restore":
            from app.core.restore import run_restore
            result = run_restore(params["source_tenant_id"], params["snapshot_ts"],
                                 params.get("selection"), "apply",
                                 params.get("actor", "api"),
                                 params.get("target_tenant_id"),
                                 note=params.get("note"), job_id=job_id)
        else:
            raise ValueError(f"unknown job kind {kind}")
        _finish(job_id, "ok", result=_trim(kind, result))
    except Exception as e:
        log.exception("job failed id=%s kind=%s tenant=%s", job_id, kind, tid)
        _finish(job_id, "failed", error=e)


def recover_stale() -> None:
    """Boot: jobs left queued/running by a previous process are dead - mark
    them failed so the UI never shows a ghost forever-running job."""
    from app.models.db import Job, SessionLocal
    with SessionLocal() as db:
        rows = db.query(Job).filter(Job.status.in_(("queued", "running"))).all()
        for j in rows:
            j.status = "failed"
            j.finished_at = _now()
            d = dict(j.detail or {})
            d["error"] = "interrupted by app restart"
            j.detail = d
        if rows:
            log.warning("marked %s interrupted job(s) failed on boot", len(rows))
        db.commit()
