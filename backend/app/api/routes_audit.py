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


def _load_pair(tenant_id: int, ts: str, request: Request):
    """View export + comparison base. ts is a snapshot timestamp, or "current"
    for the LIVE provider config (compared against the latest snapshot)."""
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        slug, key = t.slug, crypto.unwrap_data_key(t.wrapped_data_key)
    from app.core import storage as st
    snaps = st.list_snapshots(slug)
    latest = snaps[-1] if snaps else None
    if ts == "current":
        from app.core import livestate
        export = livestate.get_live_export(tenant_id)
        cur = st.read_snapshot(slug, latest, key) if latest else {}
        return export, cur, {"mode": "current", "is_latest": False, "latest": latest}
    if ts not in snaps:
        raise HTTPException(404, "snapshot not found")
    export = st.read_snapshot(slug, ts, key)
    cur = export if ts == latest else st.read_snapshot(slug, latest, key)
    return export, cur, {"mode": "snapshot", "is_latest": ts == latest, "latest": latest}


@router.get("/tenants/{tenant_id}/snapshots/{ts}/explore")
def explore(tenant_id: int, ts: str, request: Request,
            resource_type: str | None = None, q: str | None = None,
            limit: int = 500) -> dict:
    """Explorer: category grid (no resource_type) or object list with a status
    badge per object vs the LATEST snapshot (the offline 'current')."""
    import json as _json
    export, cur, info = _load_pair(tenant_id, ts, request)
    if not resource_type:
        cats = [{"resource_type": rt,
                 "count": len(export.get(rt, [])),
                 "current_count": len(cur.get(rt, []))}
                for rt in sorted(set(export) | set(cur))]
        return {**info, "categories": cats}
    if resource_type not in export and resource_type not in cur:
        raise HTTPException(404, "resource type not in this snapshot")
    ql = (q or "").lower()
    cur_idx = {obj_id(o): o for o in cur.get(resource_type, [])}
    rows, snap_ids = [], set()
    for o in export.get(resource_type, []):
        oid = obj_id(o)
        snap_ids.add(oid)
        if ql and ql not in obj_name(o).lower() and ql not in oid.lower():
            continue
        c = cur_idx.get(oid)
        if c is None:
            status = "deleted"
        elif _json.dumps(normalize(o), sort_keys=True) != _json.dumps(normalize(c), sort_keys=True):
            status = "modified"
        else:
            status = "unchanged"
        rows.append({"object_id": oid, "object_name": obj_name(o), "status": status})
    for oid, c in cur_idx.items():
        if oid in snap_ids:
            continue
        if ql and ql not in obj_name(c).lower() and ql not in oid.lower():
            continue
        rows.append({"object_id": oid, "object_name": obj_name(c), "status": "new"})
    if info["mode"] == "current":
        # In the live view, roles invert: view-only objects are NOT-YET-BACKED-UP
        # ("new"), base-only objects were DELETED since the backup.
        swap = {"deleted": "new", "new": "deleted"}
        for r in rows:
            r["status"] = swap.get(r["status"], r["status"])
    return {"resource_type": resource_type, **info,
            "total": len(rows), "objects": rows[:min(limit, 1000)]}


@router.get("/tenants/{tenant_id}/snapshots/{ts}/explore/{resource_type}/{object_id}")
def explore_object(tenant_id: int, ts: str, resource_type: str, object_id: str,
                   request: Request) -> dict:
    """Object detail: normalized config from the snapshot AND from the latest
    snapshot, plus which fields differ."""
    import json as _json
    export, cur, info = _load_pair(tenant_id, ts, request)
    snap_obj = next((o for o in export.get(resource_type, []) if obj_id(o) == object_id), None)
    cur_obj = next((o for o in cur.get(resource_type, []) if obj_id(o) == object_id), None)
    if snap_obj is None and cur_obj is None:
        raise HTTPException(404, "object not found")
    ns = normalize(snap_obj) if snap_obj else None
    nc = normalize(cur_obj) if cur_obj else None
    if snap_obj is None:
        status = "new"
    elif cur_obj is None:
        status = "deleted"
    elif _json.dumps(ns, sort_keys=True) != _json.dumps(nc, sort_keys=True):
        status = "modified"
    else:
        status = "unchanged"
    changed = []
    if ns is not None and nc is not None:
        for k in sorted(set(ns) | set(nc)):
            if _json.dumps(ns.get(k), sort_keys=True) != _json.dumps(nc.get(k), sort_keys=True):
                changed.append(k)
    if info["mode"] == "current":
        status = {"deleted": "new", "new": "deleted"}.get(status, status)
    return {"status": status, **info, "changed_fields": changed,
            "object": ns, "current": nc}
