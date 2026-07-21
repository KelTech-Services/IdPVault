"""Users & Access (identity) backup + restore preview/apply + estimates.
Write paths need write-level access (admin, or org_admin in own org);
snapshot listings need read access to the tenant."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.identity import estimate_next, plan_identity_restore
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
    with SessionLocal() as db:
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
    from app.core.jobs import enqueue
    jid = enqueue("identity_backup", tenant_id, request.state.user["username"])
    return {"job_id": jid, "status": "queued"}


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


@router.get("/tenants/{tenant_id}/identity/diff")
def identity_diff(tenant_id: int, old: str, new: str, request: Request) -> dict:
    """Per-object diff between two Users & Access snapshots: users added,
    removed, and changed (with field values), plus membership/assignment
    edges added and removed. Read-only."""
    _read(request, tenant_id)
    from app.core import crypto, storage
    from app.core.identity import _fmt_val, _ulabel, diff_identities
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        data_key = crypto.unwrap_data_key(t.wrapped_data_key)
        try:
            o = storage.read_identities(t.slug, old, data_key)
            n = storage.read_identities(t.slug, new, data_key)
        except FileNotFoundError:
            raise HTTPException(404, "identity snapshot not found")
    d = diff_identities(o, n) or {}
    CAP = 500
    out = {"old": old, "new": new, "buckets": {}}
    for bucket, ch in d.items():
        if bucket == "users":
            changed = []
            for c in ch.get("changed", [])[:CAP]:
                before, after = c.get("before", {}), c.get("after", {})
                changes = []
                bp, ap = before.get("profile"), after.get("profile")
                if isinstance(bp, dict) or isinstance(ap, dict):
                    bpp, app_ = bp or {}, ap or {}
                    for f in sorted(set(bpp) | set(app_)):
                        if bpp.get(f) != app_.get(f):
                            changes.append({"field": f, "from": _fmt_val(bpp.get(f)),
                                            "to": _fmt_val(app_.get(f))})
                for f in sorted(set(before) | set(after)):
                    if f != "profile" and before.get(f) != after.get(f):
                        changes.append({"field": f, "from": _fmt_val(before.get(f)),
                                        "to": _fmt_val(after.get(f))})
                entry = _ulabel(after) or _ulabel(before)
                entry["changes"] = changes[:25]
                changed.append(entry)
            out["buckets"]["users"] = {
                "added": [_ulabel(u) for u in ch.get("added", [])[:CAP]],
                "removed": [_ulabel(u) for u in ch.get("removed", [])[:CAP]],
                "changed": changed,
                "counts": {k: len(ch.get(k, [])) for k in ("added", "removed", "changed")}}
        else:
            out["buckets"][bucket] = {
                "added": [e.get("name") or e.get("id") for e in ch.get("added", [])[:CAP]],
                "removed": [e.get("name") or e.get("id") for e in ch.get("removed", [])[:CAP]],
                "counts": {k: len(ch.get(k, [])) for k in ("added", "removed")}}
    return out


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
    revert_selection: list[str] | None = None  # existing users whose profile reverts (opt-in only)
    note: str | None = None  # justification - recorded in restore history + alert
    password: str | None = None  # re-auth: applying a restore requires the caller's password


@router.post("/tenants/{tenant_id}/identity/restore/apply")
def restore_apply(tenant_id: int, body: IdApplyIn, request: Request) -> dict:
    _write(request, tenant_id)
    _require_identity_license(tenant_id)
    if not body.confirm:
        raise HTTPException(422, "confirm must be true to apply an identity restore")
    from app.api.routes_restore import _require_note_if_configured
    _require_note_if_configured(body.note)
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        from app.api.routes_backups import _require_reauth
        _require_reauth(db, request, body.password or "", t.slug, "identity.restore.apply")
        from app.core import storage
        if body.snapshot_ts not in storage.list_identity_snapshots(t.slug):
            raise HTTPException(404, "identity snapshot not found")
    from app.core.jobs import enqueue
    jid = enqueue("identity_restore", tenant_id, request.state.user["username"],
                  params={"snapshot_ts": body.snapshot_ts,
                          "actor": request.state.user["username"],
                          "selection": body.selection,
                          "revert_selection": body.revert_selection,
                          "note": body.note})
    return {"job_id": jid, "status": "queued"}


class IdentitySnapshotDeleteIn(BaseModel):
    timestamps: list[str]
    password: str   # re-auth: deleting backups requires the current user's password


@router.post("/tenants/{tenant_id}/identity/snapshots/delete")
def delete_identity_snapshots(tenant_id: int, body: IdentitySnapshotDeleteIn,
                              request: Request) -> dict:
    """Admin-only bulk deletion of Users & Access snapshots (files + records).
    Every deletion is audit-logged."""
    from app.core import storage
    from app.core.security import require_admin
    from app.models.db import AuditLog
    require_admin(request)
    from app.api.routes_backups import _require_reauth
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        _require_reauth(db, request, body.password, t.slug,
                        "tenant.identity_snapshots_delete")
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
