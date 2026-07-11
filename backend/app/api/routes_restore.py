"""Restore endpoints. Preview (dry-run) needs any authenticated user is too broad —
both preview and apply are admin-only since preview reads decrypted snapshots."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.restore import run_restore
from app.core.security import require_admin
from app.models.db import RestoreRun, SessionLocal, Tenant

router = APIRouter(tags=["restore"], dependencies=[Depends(require_admin)])


class RestoreSelection(BaseModel):
    resource_types: list[str] | None = None
    objects: list[dict] | None = None   # [{resource_type, object_id}]


class RestoreIn(BaseModel):
    snapshot_ts: str
    selection: RestoreSelection | None = None


@router.post("/tenants/{tenant_id}/restore/preview")
def preview(tenant_id: int, body: RestoreIn, request: Request) -> dict:
    try:
        return run_restore(tenant_id, body.snapshot_ts,
                           body.selection.model_dump() if body.selection else None,
                           "dry_run", request.state.user["username"])
    except FileNotFoundError:
        raise HTTPException(404, "snapshot not found")
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/tenants/{tenant_id}/restore/apply")
def apply(tenant_id: int, body: RestoreIn, request: Request) -> dict:
    try:
        return run_restore(tenant_id, body.snapshot_ts,
                           body.selection.model_dump() if body.selection else None,
                           "apply", request.state.user["username"])
    except FileNotFoundError:
        raise HTTPException(404, "snapshot not found")
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/tenants/{tenant_id}/restore/runs")
def runs(tenant_id: int) -> list[dict]:
    with SessionLocal() as db:
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
        rows = db.query(RestoreRun).filter(RestoreRun.tenant_id == tenant_id)\
            .order_by(RestoreRun.id.desc()).limit(50).all()
        return [{"id": r.id, "snapshot_ts": r.snapshot_ts, "mode": r.mode,
                 "actor": r.actor, "summary": r.summary, "at": r.at.isoformat()}
                for r in rows]


@router.get("/tenants/{tenant_id}/restore/runs/{run_id}")
def run_detail(tenant_id: int, run_id: int) -> dict:
    with SessionLocal() as db:
        r = db.get(RestoreRun, run_id)
        if r is None or r.tenant_id != tenant_id:
            raise HTTPException(404, "restore run not found")
        return {"id": r.id, "snapshot_ts": r.snapshot_ts, "mode": r.mode,
                "actor": r.actor, "summary": r.summary, "results": r.results,
                "at": r.at.isoformat()}
