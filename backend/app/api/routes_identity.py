"""Identity backup + dry-run restore preview + duration estimate.
Backup/preview are admin-only (they read decrypted user data)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.identity import (apply_identity_restore, estimate_next,
                                plan_identity_restore, run_identity_backup)
from app.core.security import require_admin
from app.models.db import IdentitySnapshot, SessionLocal, Tenant

router = APIRouter(tags=["identity"], dependencies=[Depends(require_admin)])


@router.post("/tenants/{tenant_id}/identity/backup")
def backup(tenant_id: int) -> dict:
    try:
        return run_identity_backup(tenant_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/tenants/{tenant_id}/identity/snapshots")
def snapshots(tenant_id: int) -> list[dict]:
    with SessionLocal() as db:
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
        rows = db.query(IdentitySnapshot).filter(IdentitySnapshot.tenant_id == tenant_id)\
            .order_by(IdentitySnapshot.id.desc()).limit(60).all()
        return [{"ts": r.ts, "counts": r.counts, "size": r.size, "api_calls": r.api_calls,
                 "duration_ms": r.duration_ms, "status": r.status, "error": r.error,
                 "at": r.at.isoformat()} for r in rows]


@router.get("/tenants/{tenant_id}/identity/estimate")
def estimate(tenant_id: int) -> dict:
    try:
        return estimate_next(tenant_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


class IdRestoreIn(BaseModel):
    snapshot_ts: str


@router.post("/tenants/{tenant_id}/identity/restore/preview")
def restore_preview(tenant_id: int, body: IdRestoreIn, request: Request) -> dict:
    try:
        return plan_identity_restore(tenant_id, body.snapshot_ts, request.state.user["username"])
    except FileNotFoundError:
        raise HTTPException(404, "identity snapshot not found")
    except ValueError as e:
        raise HTTPException(404, str(e))


class IdApplyIn(BaseModel):
    snapshot_ts: str
    confirm: bool = False  # must be true — guards against accidental writes


@router.post("/tenants/{tenant_id}/identity/restore/apply")
def restore_apply(tenant_id: int, body: IdApplyIn, request: Request) -> dict:
    if not body.confirm:
        raise HTTPException(422, "confirm must be true to apply an identity restore")
    try:
        return apply_identity_restore(tenant_id, body.snapshot_ts, request.state.user["username"])
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "identity snapshot not found")
    except ValueError as e:
        raise HTTPException(404, str(e))
