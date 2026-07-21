"""Dashboard summary + per-tenant event feed."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

from app.core import crypto
from app.models.db import BackupRun, Event, SessionLocal, Snapshot, Tenant
from app.providers import get_adapter

router = APIRouter(tags=["dashboard"])


def _ts_to_iso(ts: str) -> str:
    return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)\
        .strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/dashboard/summary")
def summary(request: Request) -> dict:
    from app.core.security import visible_tenant_ids
    now = datetime.now(timezone.utc)
    out = {"tenants": [], "coverage": {"ok": 0, "total": 0}, "storage_bytes": 0,
           "events_7d": 0}
    with SessionLocal() as db:
        vis = visible_tenant_ids(db, request.state.user)   # None = unrestricted
        tenants = [t for t in db.query(Tenant).all() if vis is None or t.id in vis]
        out["coverage"]["total"] = len(tenants)
        evq = db.query(Event).filter(Event.at >= now - timedelta(days=7))
        if vis is not None:
            evq = evq.filter(Event.tenant_id.in_(vis or {-1}))
        out["events_7d"] = evq.count()
        for t in tenants:
            last = db.query(BackupRun).filter(BackupRun.tenant_id == t.id)\
                .order_by(BackupRun.id.desc()).first()
            snaps = db.query(Snapshot).filter(Snapshot.tenant_id == t.id)
            snap_count = snaps.count()
            storage = sum(s.size for s in snaps.all())
            out["storage_bytes"] += storage
            healthy = bool(last and last.status == "ok" and
                           last.at >= now - timedelta(hours=26))
            if healthy or (last and last.status == "ok" and not t.schedule_cron):
                out["coverage"]["ok"] += 1
            unbacked = None
            if last and last.status == "ok":
                try:
                    data_key = crypto.unwrap_data_key(t.wrapped_data_key)
                    creds = crypto.decrypt(t.enc_credentials, data_key).decode()
                    unbacked = get_adapter(t.provider, t.base_url, creds)\
                        .count_changes_since(_ts_to_iso(last.ts))
                except Exception:
                    unbacked = None
            out["tenants"].append({
                "id": t.id, "name": t.name, "slug": t.slug, "provider": t.provider,
                "schedule_cron": t.schedule_cron,
                "last_run": {"ts": last.ts, "status": last.status, "error": last.error,
                             "at": last.at.isoformat()} if last else None,
                "snapshot_count": snap_count, "storage_bytes": storage,
                "unbacked_changes": unbacked,
            })
    return out


@router.get("/dashboard/trends")
def trends(request: Request, days: int = 14) -> dict:
    """Daily aggregates for the dashboard charts: change events by type,
    backup runs by outcome, and storage by tenant. Org-scoped for MSP users."""
    from app.core.security import visible_tenant_ids
    days = max(2, min(days, 90))
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    day_keys = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    with SessionLocal() as db:
        vis = visible_tenant_ids(db, request.state.user)

        def _ok(tid):
            return vis is None or tid in vis
        ev_daily = {d: {"add": 0, "update": 0, "delete": 0} for d in day_keys}
        for e in db.query(Event).filter(Event.at >= start).all():
            if not _ok(e.tenant_id):
                continue
            d = e.at.strftime("%Y-%m-%d")
            if d in ev_daily and e.event_type in ev_daily[d]:
                ev_daily[d][e.event_type] += 1
        run_daily = {d: {"ok": 0, "failed": 0} for d in day_keys}
        for r in db.query(BackupRun).filter(BackupRun.at >= start).all():
            if not _ok(r.tenant_id):
                continue
            d = r.at.strftime("%Y-%m-%d")
            if d in run_daily:
                run_daily[d]["ok" if r.status == "ok" else "failed"] += 1
        names = {t.id: t.name for t in db.query(Tenant).all()}
        storage: dict[int, int] = {}
        for s in db.query(Snapshot).all():
            if not _ok(s.tenant_id):
                continue
            storage[s.tenant_id] = storage.get(s.tenant_id, 0) + s.size
    return {"days": day_keys,
            "events_daily": [{"date": d, **ev_daily[d]} for d in day_keys],
            "runs_daily": [{"date": d, **run_daily[d]} for d in day_keys],
            "storage_by_tenant": [{"name": names.get(tid, str(tid)), "bytes": b}
                                  for tid, b in sorted(storage.items(), key=lambda x: -x[1])]}


@router.get("/tenants/{tenant_id}/events")
def tenant_events(tenant_id: int, request: Request, limit: int = 100, offset: int = 0,
                  resource_type: str | None = None, event_type: str | None = None,
                  q: str | None = None) -> dict:
    from app.core.security import require_tenant_read
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
        qy = db.query(Event).filter(Event.tenant_id == tenant_id)
        if resource_type:
            qy = qy.filter(Event.resource_type == resource_type)
        if event_type:
            qy = qy.filter(Event.event_type == event_type)
        if q and q.strip():
            like = f"%{q.strip()}%"
            qy = qy.filter((Event.object_name.ilike(like)) | (Event.object_id.ilike(like)))
        total = qy.count()
        rows = qy.order_by(Event.id.desc()).offset(offset).limit(min(limit, 500)).all()
        return {"total": total, "events": [
            {"id": e.id, "snapshot_ts": e.snapshot_ts, "event_type": e.event_type,
             "resource_type": e.resource_type, "object_id": e.object_id,
             "object_name": e.object_name, "detail": e.detail, "at": e.at.isoformat()}
            for e in rows]}


_IDENTITY_TYPES = {"users", "group_memberships", "app_group_assignments",
                   "app_user_assignments_direct"}


@router.get("/tenants/{tenant_id}/objects/search")
def object_search(tenant_id: int, q: str, request: Request, kind: str | None = None) -> dict:
    """Object timeline search over the events lane: for each object matching q,
    its change history plus - if its latest event is a delete - the snapshot to
    restore it from (the one just before the deletion was detected). Objects
    that never changed while IdPVault was watching have no events and so do
    not appear (that is documented in the UI)."""
    from app.core.security import require_tenant_read
    if len(q.strip()) < 2:
        return {"objects": [], "truncated": False}
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        like = f"%{q.strip()}%"
        ev_q = db.query(Event).filter(Event.tenant_id == tenant_id).filter(
            (Event.object_name.ilike(like)) | (Event.object_id.ilike(like)))
        if kind == "identity":
            ev_q = ev_q.filter(Event.resource_type.in_(_IDENTITY_TYPES))
        elif kind == "config":
            ev_q = ev_q.filter(~Event.resource_type.in_(_IDENTITY_TYPES))
        rows = ev_q.order_by(Event.id.desc()).limit(400).all()
        slug = t.slug
    from app.core import storage
    cfg_snaps = storage.list_snapshots(slug)
    id_snaps = storage.list_identity_snapshots(slug)
    groups: dict = {}
    for e in rows:  # newest-first, so events[0] per group is the latest
        key = (e.resource_type, e.object_id or e.object_name)
        g = groups.setdefault(key, {
            "resource_type": e.resource_type, "object_id": e.object_id,
            "object_name": e.object_name,
            "kind": "identity" if e.resource_type in _IDENTITY_TYPES else "config",
            "events": []})
        if len(g["events"]) < 20:
            g["events"].append({"at": e.at.isoformat(), "event_type": e.event_type,
                                "snapshot_ts": e.snapshot_ts,
                                "fields": (e.detail or {}).get("fields", [])})
        if not g["object_name"] and e.object_name:
            g["object_name"] = e.object_name
    out = []
    for g in list(groups.values())[:20]:
        latest = g["events"][0]
        g["deleted"] = latest["event_type"] == "delete"
        g["restore_from"] = None
        if g["deleted"]:
            snaps = id_snaps if g["kind"] == "identity" else cfg_snaps
            if latest["snapshot_ts"] in snaps:
                i = snaps.index(latest["snapshot_ts"])
                g["restore_from"] = snaps[i - 1] if i > 0 else None
            else:  # detecting snapshot pruned - newest retained snapshot older than it
                older = [s for s in snaps if s < latest["snapshot_ts"]]
                g["restore_from"] = older[-1] if older else None
        out.append(g)
    return {"objects": out, "truncated": len(groups) > 20}


@router.get("/runs")
def recent_runs(request: Request, limit: int = 50) -> list[dict]:
    from app.core.security import visible_tenant_ids
    with SessionLocal() as db:
        vis = visible_tenant_ids(db, request.state.user)
        rows = db.query(BackupRun).order_by(BackupRun.id.desc()).limit(min(limit, 200)).all()
        names = {t.id: t.name for t in db.query(Tenant).all()}
        return [{"id": r.id, "tenant_id": r.tenant_id, "tenant": names.get(r.tenant_id, "?"),
                 "ts": r.ts, "status": r.status, "error": r.error,
                 "duration_ms": r.duration_ms, "at": r.at.isoformat()}
                for r in rows if vis is None or r.tenant_id in vis]
