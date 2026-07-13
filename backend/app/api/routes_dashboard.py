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
                  resource_type: str | None = None, event_type: str | None = None) -> dict:
    from app.core.security import require_tenant_read
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
        q = db.query(Event).filter(Event.tenant_id == tenant_id)
        if resource_type:
            q = q.filter(Event.resource_type == resource_type)
        if event_type:
            q = q.filter(Event.event_type == event_type)
        total = q.count()
        rows = q.order_by(Event.id.desc()).offset(offset).limit(min(limit, 500)).all()
        return {"total": total, "events": [
            {"id": e.id, "snapshot_ts": e.snapshot_ts, "event_type": e.event_type,
             "resource_type": e.resource_type, "object_id": e.object_id,
             "object_name": e.object_name, "detail": e.detail, "at": e.at.isoformat()}
            for e in rows]}


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
