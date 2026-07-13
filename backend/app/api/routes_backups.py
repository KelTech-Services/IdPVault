from fastapi import APIRouter, HTTPException, Request
from app.core.security import require_tenant_read, require_tenant_write

from app.core import crypto, storage
from app.core.diff import diff_exports
from app.core.scheduler import run_backup
from app.models.db import SessionLocal, Tenant

router = APIRouter(tags=["backups"])


@router.post("/tenants/{tenant_id}/backup")
def trigger_backup(tenant_id: int, request: Request) -> dict:
    from app.core import license as lic
    with SessionLocal() as db:
        require_tenant_write(request, db, tenant_id)
    if not lic.is_tenant_entitled(tenant_id):
        raise HTTPException(402, "this tenant is over your license's tenant limit — "
                                 "backups are paused for it until a license is added "
                                 "in Settings → License")
    result = run_backup(tenant_id)
    return {"manifest": result["manifest"], "drift_detected": bool(result["drift"])}


@router.get("/tenants/{tenant_id}/snapshots")
def snapshots(tenant_id: int, request: Request) -> list[str]:
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        return storage.list_snapshots(t.slug)


@router.get("/tenants/{tenant_id}/diff")
def diff(tenant_id: int, old: str, new: str, request: Request) -> dict:
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        key = crypto.unwrap_data_key(t.wrapped_data_key)
        return diff_exports(
            storage.read_snapshot(t.slug, old, key),
            storage.read_snapshot(t.slug, new, key),
        )
