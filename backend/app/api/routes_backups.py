"""Config backups: trigger, snapshot list (with manifest metadata), compare,
per-snapshot change summary (cached), and admin-only bulk deletion."""
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.security import require_admin, require_tenant_read, require_tenant_write

from app.core import crypto, storage
from app.core.diff import diff_exports
from app.core.scheduler import run_backup
from app.models.db import AuditLog, SessionLocal, Tenant

router = APIRouter(tags=["backups"])


@router.post("/tenants/{tenant_id}/backup")
def trigger_backup(tenant_id: int, request: Request) -> dict:
    from app.core import license as lic
    with SessionLocal() as db:
        require_tenant_write(request, db, tenant_id)
    if not lic.is_tenant_entitled(tenant_id):
        raise HTTPException(402, "this tenant is over your license's tenant limit - "
                                 "backups are paused for it until a license is added "
                                 "in Settings → License")
    result = run_backup(tenant_id)
    return {"manifest": result["manifest"], "drift_detected": bool(result["drift"])}


@router.get("/tenants/{tenant_id}/snapshots")
def snapshots(tenant_id: int, request: Request) -> list[dict]:
    """Snapshot list enriched from each snapshot's plaintext manifest (no
    decryption): object totals, encrypted size, and Full-DR dump size."""
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        slug = t.slug
    out = []
    for ts in storage.list_snapshots(slug):
        m = storage.read_manifest(slug, ts) or {}
        entry = {"ts": ts,
                 "objects": sum((m.get("counts") or {}).values()),
                 "size": m.get("size_encrypted", 0),
                 "db_dump_size": None}
        dump = os.path.join(storage.snapshot_dir(slug, ts), "pgdump.sql.enc")
        if os.path.exists(dump):
            entry["db_dump_size"] = os.path.getsize(dump)
        out.append(entry)
    return out


@router.get("/tenants/{tenant_id}/snapshots/{ts}/changes")
def snapshot_changes(tenant_id: int, ts: str, request: Request) -> dict:
    """Added/removed/changed totals vs the previous snapshot. Computed once
    per pair and cached inside the snapshot dir, so lists render fast."""
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        slug, key = t.slug, crypto.unwrap_data_key(t.wrapped_data_key)
    snaps = storage.list_snapshots(slug)
    if ts not in snaps:
        raise HTTPException(404, "snapshot not found")
    i = snaps.index(ts)
    if i == 0:
        return {"first": True}
    prev = snaps[i - 1]
    cached = storage.read_changes_cache(slug, ts)
    if cached and cached.get("prev") == prev:
        return cached
    d = diff_exports(storage.read_snapshot(slug, prev, key),
                     storage.read_snapshot(slug, ts, key))
    out = {"prev": prev,
           "added": sum(len(x["added"]) for x in d.values()),
           "removed": sum(len(x["removed"]) for x in d.values()),
           "changed": sum(len(x["changed"]) for x in d.values())}
    storage.write_changes_cache(slug, ts, out)
    return out


class SnapshotDeleteIn(BaseModel):
    timestamps: list[str]
    password: str   # re-auth: deleting backups requires the current user's password


def _require_reauth(db, request: Request, password: str, slug: str, action: str) -> None:
    """Destructive operations re-verify the caller's password. A wrong password
    is audit-logged and rejected."""
    from app.core.security import verify_password
    from app.models.db import User
    u = db.get(User, request.state.user["id"])
    if u is None or not verify_password(password or "", u.password_hash):
        db.add(AuditLog(action=action + "_denied",
                        detail={"slug": slug, "reason": "password re-auth failed"}))
        db.commit()
        raise HTTPException(403, "password incorrect - deletion requires your password")


@router.post("/tenants/{tenant_id}/snapshots/delete", dependencies=[Depends(require_admin)])
def delete_snapshots(tenant_id: int, body: SnapshotDeleteIn, request: Request) -> dict:
    """Admin-only bulk deletion of config snapshots (files incl. Full-DR dumps).
    Requires password re-auth; every deletion is audit-logged."""
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        slug = t.slug
        _require_reauth(db, request, body.password, slug, "tenant.snapshots_delete")
        for ts in body.timestamps:
            try:
                storage._safe_ts(ts)
            except ValueError:
                raise HTTPException(422, "invalid snapshot timestamp")
        existing = set(storage.list_snapshots(slug))
        doomed = [ts for ts in body.timestamps if ts in existing]
        for ts in doomed:
            storage.delete_snapshot(slug, ts)
        db.add(AuditLog(action="tenant.snapshots_delete",
                        detail={"slug": slug, "count": len(doomed),
                                "timestamps": doomed[:20]}))
        db.commit()
    return {"deleted": doomed}


@router.get("/tenants/{tenant_id}/state/summary")
def state_summary(tenant_id: int, request: Request) -> dict:
    """Cached live-state summary (counts, drift vs latest snapshot, freshness)."""
    from app.models.db import TenantState
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
        st = db.get(TenantState, tenant_id)
        if st is None or st.checked_at is None:
            return {"available": False}
        return {"available": True, "checked_at": st.checked_at.isoformat(),
                **(st.summary or {})}


@router.post("/tenants/{tenant_id}/state/refresh")
def state_refresh(tenant_id: int, request: Request) -> dict:
    """Manual 'Refresh from provider' (debounced server-side)."""
    from app.core import livestate
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
    summary = livestate.poll_tenant(tenant_id, force=True)
    if summary is None:
        raise HTTPException(402, "this tenant is over your license's tenant limit")
    from app.models.db import TenantState
    with SessionLocal() as db:
        st = db.get(TenantState, tenant_id)
        return {"available": True,
                "checked_at": st.checked_at.isoformat() if st and st.checked_at else None,
                **summary}


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
