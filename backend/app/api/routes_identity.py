"""Users & Access (identity) backup + restore preview/apply + estimates.
Write paths need write-level access (admin, or org_admin in own org);
snapshot listings need read access to the tenant."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.identity import (apply_identity_restore, estimate_next,
                                plan_identity_restore, run_identity_backup)
from app.core.security import require_tenant_read, require_tenant_write
from app.models.db import IdentitySnapshot, SessionLocal, Tenant

router = APIRouter(tags=["identity"])


def _read(request: Request, tenant_id: int) -> None:
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)


def _write(request: Request, tenant_id: int) -> None:
    with SessionLocal() as db:
        require_tenant_write(request, db, tenant_id)


def _require_identity_license(tenant_id: int) -> None:
    """Identity backup/restore is a paid feature; existing identity snapshots
    stay viewable regardless."""
    from app.core import license as lic
    if not lic.has_feature("identity") or not lic.is_tenant_entitled(tenant_id):
        raise HTTPException(402, "identity backup & restore requires a paid license - "
                                 "add one in Settings → License")


def _require_identity_supported(tenant_id: int) -> None:
    from app.providers import identity_supported
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
    if t is not None and not identity_supported(t.provider):
        raise HTTPException(422, f"identity backup isn't supported for {t.provider} yet")


@router.post("/tenants/{tenant_id}/identity/backup")
def backup(tenant_id: int, request: Request) -> dict:
    _write(request, tenant_id)
    _require_identity_license(tenant_id)
    _require_identity_supported(tenant_id)
    try:
        return run_identity_backup(tenant_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/tenants/{tenant_id}/identity/snapshots")
def snapshots(tenant_id: int, request: Request) -> list[dict]:
    _read(request, tenant_id)
    with SessionLocal() as db:
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
        rows = db.query(IdentitySnapshot).filter(IdentitySnapshot.tenant_id == tenant_id)\
            .order_by(IdentitySnapshot.id.desc()).limit(60).all()
        return [{"ts": r.ts, "counts": r.counts, "size": r.size, "api_calls": r.api_calls,
                 "duration_ms": r.duration_ms, "status": r.status, "error": r.error,
                 "at": r.at.isoformat()} for r in rows]


@router.get("/tenants/{tenant_id}/identity/estimate")
def estimate(tenant_id: int, request: Request) -> dict:
    _read(request, tenant_id)
    try:
        return estimate_next(tenant_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


class IdRestoreIn(BaseModel):
    snapshot_ts: str


@router.post("/tenants/{tenant_id}/identity/restore/preview")
def restore_preview(tenant_id: int, body: IdRestoreIn, request: Request) -> dict:
    _write(request, tenant_id)
    _require_identity_license(tenant_id)
    try:
        return plan_identity_restore(tenant_id, body.snapshot_ts, request.state.user["username"])
    except FileNotFoundError:
        raise HTTPException(404, "identity snapshot not found")
    except ValueError as e:
        raise HTTPException(404, str(e))


class IdApplyIn(BaseModel):
    snapshot_ts: str
    confirm: bool = False  # must be true — guards against accidental writes
    selection: list[str] | None = None  # user natural keys to recreate; None = all missing


@router.post("/tenants/{tenant_id}/identity/restore/apply")
def restore_apply(tenant_id: int, body: IdApplyIn, request: Request) -> dict:
    _write(request, tenant_id)
    _require_identity_license(tenant_id)
    if not body.confirm:
        raise HTTPException(422, "confirm must be true to apply an identity restore")
    try:
        return apply_identity_restore(tenant_id, body.snapshot_ts,
                                      request.state.user["username"], body.selection)
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "identity snapshot not found")
    except ValueError as e:
        raise HTTPException(404, str(e))


class IdentitySnapshotDeleteIn(BaseModel):
    timestamps: list[str]


@router.post("/tenants/{tenant_id}/identity/snapshots/delete")
def delete_identity_snapshots(tenant_id: int, body: IdentitySnapshotDeleteIn,
                              request: Request) -> dict:
    """Admin-only bulk deletion of Users & Access snapshots (files + records).
    Every deletion is audit-logged."""
    from app.core import storage
    from app.core.security import require_admin
    from app.models.db import AuditLog
    require_admin(request)
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        for ts in body.timestamps:
            try:
                storage._safe_ts(ts)
            except ValueError:
                raise HTTPException(422, "invalid snapshot timestamp")
        rows = db.query(IdentitySnapshot)\
            .filter(IdentitySnapshot.tenant_id == tenant_id,
                    IdentitySnapshot.ts.in_(body.timestamps)).all()
        doomed = []
        for r in rows:
            storage.delete_identity_snapshot(t.slug, r.ts)
            doomed.append(r.ts)
            db.delete(r)
        db.add(AuditLog(action="tenant.identity_snapshots_delete",
                        detail={"slug": t.slug, "count": len(doomed),
                                "timestamps": doomed[:20]}))
        db.commit()
    return {"deleted": doomed}
