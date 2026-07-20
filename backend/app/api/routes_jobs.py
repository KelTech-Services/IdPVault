"""Job status endpoints for the nav activity area and page-level progress.
Org-scoped users only ever see jobs for tenants they can see."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

from app.core.security import visible_tenant_ids
from app.models.db import Job, SessionLocal, Tenant

router = APIRouter(tags=["jobs"])

# finished jobs stay in /jobs/active briefly so the UI can show completion
_LINGER_SECS = 15


def _row(j: Job, tenant_names: dict) -> dict:
    detail = j.detail or {}
    return {
        "id": j.id, "tenant_id": j.tenant_id,
        "tenant_name": tenant_names.get(j.tenant_id, f"tenant {j.tenant_id}"),
        "kind": j.kind, "trigger": j.trigger, "status": j.status,
        "progress_done": j.progress_done, "progress_total": j.progress_total,
        "error": detail.get("error"), "result": detail.get("result"),
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


def _names(db, ids) -> dict:
    if not ids:
        return {}
    rows = db.query(Tenant.id, Tenant.name).filter(Tenant.id.in_(ids)).all()
    return {i: n for i, n in rows}


@router.get("/jobs/active")
def active(request: Request) -> list[dict]:
    """Queued + running jobs, plus jobs finished in the last few seconds
    (so the UI can render completion before the entry disappears)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_LINGER_SECS)
    with SessionLocal() as db:
        vis = visible_tenant_ids(db, request.state.user)
        q = db.query(Job).filter(
            (Job.status.in_(("queued", "running"))) | (Job.finished_at >= cutoff))
        if vis is not None:
            q = q.filter(Job.tenant_id.in_(vis))
        rows = q.order_by(Job.id.asc()).limit(50).all()
        names = _names(db, {j.tenant_id for j in rows})
        return [_row(j, names) for j in rows]


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, request: Request) -> dict:
    with SessionLocal() as db:
        j = db.get(Job, job_id)
        if j is None:
            raise HTTPException(404, "job not found")
        vis = visible_tenant_ids(db, request.state.user)
        if vis is not None and j.tenant_id not in vis:
            raise HTTPException(404, "job not found")   # out-of-org = 404, never 403
        names = _names(db, {j.tenant_id})
        return _row(j, names)
