"""Audit log viewer (admin) + snapshot object browser (any authenticated user)."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core import crypto, storage
from app.core.diff import normalize
from app.core.events import _id as obj_id, _name as obj_name
from app.core.security import require_admin, require_tenant_read
from app.models.db import AuditLog, SessionLocal, Tenant

router = APIRouter(tags=["audit"])


@router.get("/audit", dependencies=[Depends(require_admin)])
def audit(limit: int = 100, offset: int = 0, action: str | None = None) -> dict:
    with SessionLocal() as db:
        q = db.query(AuditLog)
        if action:
            q = q.filter(AuditLog.action.like(f"{action}%"))
        total = q.count()
        rows = q.order_by(AuditLog.id.desc()).offset(offset).limit(min(limit, 500)).all()
        return {"total": total, "entries": [
            {"id": a.id, "at": a.at.isoformat(), "actor": a.actor,
             "action": a.action, "detail": a.detail} for a in rows]}


@router.get("/tenants/{tenant_id}/snapshots/{ts}/objects")
def browse(request: Request, tenant_id: int, ts: str, resource_type: str | None = None,
           q: str | None = None, limit: int = 100) -> dict:
    """Browse a snapshot's contents. Without resource_type: type list w/ counts.
    With resource_type: object summaries (id, name), filtered by q."""
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)   # org users: 404 outside their org
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        data_key = crypto.unwrap_data_key(t.wrapped_data_key)
    try:
        export = storage.read_snapshot(t.slug, ts, data_key)
    except FileNotFoundError:
        raise HTTPException(404, "snapshot not found")
    if not resource_type:
        return {"types": [{"resource_type": k, "count": len(v)}
                          for k, v in sorted(export.items())]}
    objs = export.get(resource_type)
    if objs is None:
        raise HTTPException(404, "resource type not in this snapshot")
    ql = (q or "").lower()
    hits = [o for o in objs
            if not ql or ql in obj_name(o).lower() or ql in obj_id(o).lower()]
    return {"resource_type": resource_type, "total": len(hits),
            "objects": [{"object_id": obj_id(o), "object_name": obj_name(o)}
                        for o in hits[:min(limit, 500)]]}


@router.get("/tenants/{tenant_id}/snapshots/{ts}/objects/{resource_type}/{object_id}")
def object_detail(request: Request, tenant_id: int, ts: str, resource_type: str, object_id: str) -> dict:
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)   # org users: 404 outside their org
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        data_key = crypto.unwrap_data_key(t.wrapped_data_key)
    try:
        export = storage.read_snapshot(t.slug, ts, data_key)
    except FileNotFoundError:
        raise HTTPException(404, "snapshot not found")
    for o in export.get(resource_type, []):
        if obj_id(o) == object_id:
            return {"object": normalize(o)}
    raise HTTPException(404, "object not found")
