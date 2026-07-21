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
            limit: int = 500, offset: int = 0) -> dict:
    """Explorer: category grid (no resource_type) or object list with a status
    badge per object vs the LATEST snapshot (the offline 'current')."""
    import json as _json
    export, cur, info = _load_pair(tenant_id, ts, request)
    if not resource_type:
        cats = [{"resource_type": rt,
                 "count": len(export.get(rt, [])),
                 "current_count": len(cur.get(rt, []))}
                for rt in sorted(set(export) | set(cur))]
        out = {**info, "categories": cats}
        if info["mode"] == "current":
            u = _users_rail_info(tenant_id)
            if u is not None:
                out["users"] = u
        return out
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
    sc = {"new": 0, "modified": 0, "deleted": 0}
    for r in rows:
        if r["status"] in sc:
            sc[r["status"]] += 1
    rows.sort(key=lambda r: (r["object_name"] or "").lower())
    lim = min(limit, 1000)
    off = max(0, offset)
    return {"resource_type": resource_type, **info, "total": len(rows),
            "offset": off, "status_counts": sc, "objects": rows[off:off + lim]}


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


# ---- Live State users (v1.2): lazy live directory vs latest Users & Access snapshot ----

def _uemail(u: dict) -> str:
    prof = u.get("profile") or {}
    return prof.get("email") or u.get("email") or ""


def _users_rail_info(tenant_id: int) -> dict | None:
    """Directory > Users rail entry: count from the warm in-memory live cache,
    else from the latest Users & Access snapshot manifest. NO provider hit
    here - the live users fetch is lazy (first click / manual refresh)."""
    import json as _json
    import os as _os
    from app.core import license as lic
    from app.core import livestate
    from app.core import storage as st
    if not lic.has_feature("identity") or not lic.is_tenant_entitled(tenant_id):
        return None
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            return None
        slug = t.slug
    cached = livestate.live_identity_cached(tenant_id)
    if cached is not None:
        return {"count": len(cached.get("users", [])), "cached": True}
    snaps = st.list_identity_snapshots(slug)
    if snaps:
        try:
            with open(_os.path.join(st.identity_dir(slug, snaps[-1]), "manifest.json")) as f:
                return {"count": (_json.load(f).get("counts") or {}).get("users"),
                        "cached": False}
        except OSError:
            pass
    return {"count": None, "cached": False}


def _users_pair(tenant_id: int, request: Request, force: bool = False):
    """Live user directory + latest Users & Access snapshot, license-gated."""
    from app.core import license as lic
    from app.core import livestate
    from app.core import storage as st
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        slug, key = t.slug, crypto.unwrap_data_key(t.wrapped_data_key)
    if not lic.has_feature("identity") or not lic.is_tenant_entitled(tenant_id):
        raise HTTPException(402, "Users & Access requires a Business or MSP license")
    live = livestate.get_live_identity(tenant_id, force=force)
    snaps = st.list_identity_snapshots(slug)
    latest = snaps[-1] if snaps else None
    snap = st.read_identities(slug, latest, key) if latest else {}
    return live, snap, latest


@router.get("/tenants/{tenant_id}/live/users")
def live_users(tenant_id: int, request: Request, q: str | None = None,
               limit: int = 500, offset: int = 0) -> dict:
    """Live State > Users: the current directory with a status per user vs the
    latest Users & Access snapshot (backed up / changed / not backed up yet /
    deleted since backup)."""
    import json as _json
    from app.core.identity import _ukey
    live, snap, latest = _users_pair(tenant_id, request)
    lus, sus = live.get("users", []), snap.get("users", [])
    sidx = {str(u.get("id")): u for u in sus}
    rows, live_ids = [], set()
    for u in lus:
        uid = str(u.get("id"))
        live_ids.add(uid)
        s = sidx.get(uid)
        if s is None:
            status = "new"
        elif _json.dumps(u, sort_keys=True) != _json.dumps(s, sort_keys=True):
            status = "modified"
        else:
            status = "unchanged"
        rows.append({"object_id": uid, "object_name": _ukey(u),
                     "email": _uemail(u), "key": _ukey(u), "status": status})
    for uid, s in sidx.items():
        if uid in live_ids:
            continue
        rows.append({"object_id": uid, "object_name": _ukey(s),
                     "email": _uemail(s), "key": _ukey(s), "status": "deleted"})
    counts = {"added": sum(1 for r in rows if r["status"] == "new"),
              "removed": sum(1 for r in rows if r["status"] == "deleted"),
              "changed": sum(1 for r in rows if r["status"] == "modified")}
    ql = (q or "").lower()
    if ql:
        rows = [r for r in rows if ql in (r["object_name"] or "").lower()
                or ql in (r["email"] or "").lower() or ql in r["object_id"].lower()]
    rows.sort(key=lambda r: (r["object_name"] or "").lower())
    lim = min(limit, 1000)
    off = max(0, offset)
    return {"mode": "current", "latest_identity_snapshot": latest,
            "count": len(lus), "counts": counts, "offset": off,
            "total": len(rows), "objects": rows[off:off + lim]}


@router.post("/tenants/{tenant_id}/live/users/refresh")
def live_users_refresh(tenant_id: int, request: Request) -> dict:
    """Manual 'Refresh Users from provider' (debounced server-side). Also
    recomputes the Unbacked Users & Access changes card from the fresh data."""
    from app.core import livestate
    live, _snap, _latest = _users_pair(tenant_id, request, force=True)
    try:
        livestate.refresh_identity_section(tenant_id)
    except Exception:
        pass   # card refresh is best-effort; the directory refresh succeeded
    return {"ok": True, "count": len(live.get("users", []))}


@router.get("/tenants/{tenant_id}/live/users/{user_id}")
def live_user_detail(tenant_id: int, user_id: str, request: Request) -> dict:
    """Live State user detail: live record vs the latest Users & Access
    snapshot, plus which fields differ."""
    import json as _json
    from app.core.identity import _ukey
    live, snap, latest = _users_pair(tenant_id, request)
    lu = next((u for u in live.get("users", []) if str(u.get("id")) == user_id), None)
    su = next((u for u in snap.get("users", []) if str(u.get("id")) == user_id), None)
    if lu is None and su is None:
        raise HTTPException(404, "user not found")
    if lu is None:
        status = "deleted"
    elif su is None:
        status = "new"
    elif _json.dumps(lu, sort_keys=True) != _json.dumps(su, sort_keys=True):
        status = "modified"
    else:
        status = "unchanged"
    changed = []
    if lu is not None and su is not None:
        for k in sorted(set(lu) | set(su)):
            if _json.dumps(lu.get(k), sort_keys=True) != _json.dumps(su.get(k), sort_keys=True):
                changed.append(k)
    return {"status": status, "mode": "current", "latest_identity_snapshot": latest,
            "changed_fields": changed, "key": _ukey(su or lu),
            "object": lu, "current": su}


@router.get("/tenants/{tenant_id}/live/search")
def live_search(tenant_id: int, q: str, request: Request, limit: int = 200) -> dict:
    """Global Live State search: every config category at once (name or id
    substring), plus users when the live user directory cache is warm - a cold
    user cache is never fetched here so a keystroke can't hit the user APIs."""
    from app.core import license as lic
    from app.core import livestate
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
    ql = (q or "").strip().lower()
    if not ql:
        return {"results": [], "total": 0, "users_included": False}
    export = livestate.get_live_export(tenant_id)
    results = []
    for rt in sorted(export):
        for o in export.get(rt, []):
            name, oid = obj_name(o), obj_id(o)
            if ql in name.lower() or ql in oid.lower():
                results.append({"category": rt, "object_id": oid, "object_name": name})
    users_included = False
    cached = livestate.live_identity_cached(tenant_id)
    if cached is not None and lic.has_feature("identity") and lic.is_tenant_entitled(tenant_id):
        from app.core.identity import _ukey
        users_included = True
        for u in cached.get("users", []):
            key, email, uid = _ukey(u), _uemail(u), str(u.get("id"))
            if ql in key.lower() or ql in (email or "").lower() or ql in uid.lower():
                results.append({"category": "users", "object_id": uid, "object_name": key})
    results.sort(key=lambda r: (r["category"], (r["object_name"] or "").lower()))
    return {"results": results[:min(limit, 500)], "total": len(results),
            "users_included": users_included}
