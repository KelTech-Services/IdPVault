"""Live-state engine (v1.2 Phase 3): periodically compare each tenant's CURRENT
provider config against its latest snapshot and cache a small summary
(per-category counts + drift totals + freshness timestamp).

Good-citizen rules:
- Runs on the same serial scheduler pool as backups (never competes with them).
- Skips the provider entirely when the latest backup is fresher than the TTL
  (the snapshot IS current enough) - the summary is then derived offline.
- Manual refresh is debounced to once per minute per tenant.
- User-class objects are never polled here (config export only).

Setting: `state_poll_minutes` in Settings -> general (default 15, 0 disables).
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_REFRESH_DEBOUNCE_S = 60


def _ttl_minutes() -> int:
    from app.models.db import SessionLocal, Setting
    try:
        with SessionLocal() as db:
            row = db.get(Setting, "general")
            v = (dict(row.value) if row else {}).get("state_poll_minutes")
        v = int(v) if v is not None else 15
        return max(0, v)
    except Exception:
        return 15


def _snap_dt(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def poll_tenant(tenant_id: int, force: bool = False) -> dict | None:
    """Refresh one tenant's live-state summary. Returns the summary, or the
    cached one when fresh enough / debounced. None if the tenant is gone or
    not entitled."""
    from app.core import crypto, storage
    from app.core import license as lic
    from app.core.diff import diff_exports
    from app.models.db import SessionLocal, Tenant, TenantState
    from app.providers import get_adapter

    if not lic.is_tenant_entitled(tenant_id):
        return None
    now = datetime.now(timezone.utc)
    ttl_s = _ttl_minutes() * 60
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            return None
        st = db.get(TenantState, tenant_id)
        if st is not None and st.checked_at is not None:
            age = (now - st.checked_at).total_seconds()
            if not force and ttl_s and age < ttl_s:
                return st.summary            # fresh enough, no provider hit
            if force and age < _REFRESH_DEBOUNCE_S:
                return st.summary            # debounce manual refresh
        key = crypto.unwrap_data_key(t.wrapped_data_key)
        creds = crypto.decrypt(t.enc_credentials, key).decode()
        slug, provider, base_url = t.slug, t.provider, t.base_url

    snaps = storage.list_snapshots(slug)
    latest = snaps[-1] if snaps else None

    # Fresh backup = snapshot is current enough; derive the summary offline.
    if latest and not force:
        dt = _snap_dt(latest)
        if dt is not None and (now - dt).total_seconds() < max(ttl_s, _REFRESH_DEBOUNCE_S):
            m = storage.read_manifest(slug, latest) or {}
            summary = {"source": "snapshot", "latest_snapshot": latest,
                       "counts": m.get("counts") or {}, "categories": {},
                       "drift": {"added": 0, "removed": 0, "changed": 0}}
            _store(tenant_id, now, summary)
            return summary

    adapter = get_adapter(provider, base_url, creds)
    live = adapter.export()
    counts = {k: len(v) for k, v in live.items()}
    summary = {"source": "live", "latest_snapshot": latest,
               "counts": counts, "categories": {},
               "drift": {"added": 0, "removed": 0, "changed": 0} if latest else None}
    if latest:
        base = storage.read_snapshot(slug, latest, key)
        d = diff_exports(base, live)
        summary["categories"] = {
            rt: {"added": len(x["added"]), "removed": len(x["removed"]),
                 "changed": len(x["changed"])}
            for rt, x in d.items()}
        summary["drift"] = {
            "added": sum(len(x["added"]) for x in d.values()),
            "removed": sum(len(x["removed"]) for x in d.values()),
            "changed": sum(len(x["changed"]) for x in d.values())}
    _store(tenant_id, datetime.now(timezone.utc), summary)
    log.info("live-state poll tenant=%s source=%s drift=%s", slug,
             summary["source"], summary.get("drift"))
    return summary


def _store(tenant_id: int, checked_at: datetime, summary: dict) -> None:
    from app.models.db import SessionLocal, TenantState
    with SessionLocal() as db:
        st = db.get(TenantState, tenant_id)
        if st is None:
            st = TenantState(tenant_id=tenant_id)
            db.add(st)
        st.checked_at = checked_at
        st.summary = summary
        db.commit()


def sweep() -> None:
    """Scheduler entry point: refresh every tenant whose summary is stale."""
    ttl = _ttl_minutes()
    if ttl <= 0:
        return
    from app.models.db import SessionLocal, Tenant, TenantState
    with SessionLocal() as db:
        ids = [t.id for t in db.query(Tenant).all()]
        checked = {s.tenant_id: s.checked_at for s in db.query(TenantState).all()}
    now = datetime.now(timezone.utc)
    for tid in ids:
        ca = checked.get(tid)
        if ca is not None and (now - ca).total_seconds() < ttl * 60:
            continue
        try:
            poll_tenant(tid)
        except Exception:
            log.warning("live-state poll failed tenant=%s", tid, exc_info=True)
