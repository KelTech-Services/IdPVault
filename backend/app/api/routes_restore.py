"""Restore endpoints. Restores WRITE to live tenants, so every route requires
write-level access: global admin, or org_admin within their own org (MSP)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.restore import run_restore
from app.core.security import require_tenant_write
from app.models.db import RestoreRun, SessionLocal, Tenant

router = APIRouter(tags=["restore"])


def _require_access(request: Request, *tenant_ids) -> None:
    with SessionLocal() as db:
        for tid in tenant_ids:
            if tid is not None:
                require_tenant_write(request, db, tid)


def _require_entitled(*tenant_ids) -> None:
    from app.core import license as lic
    for tid in tenant_ids:
        if tid is not None and not lic.is_tenant_entitled(tid):
            raise HTTPException(402, "this tenant is over your license's tenant limit - "
                                     "restore is paused for it until a license is added "
                                     "in Settings → License")


class RestoreSelection(BaseModel):
    resource_types: list[str] | None = None
    objects: list[dict] | None = None   # [{resource_type, object_id}]


class RestoreIn(BaseModel):
    snapshot_ts: str
    selection: RestoreSelection | None = None
    target_tenant_id: int | None = None  # set = clone/promote into another tenant
    note: str | None = None  # justification - recorded in restore history + alert
    password: str | None = None  # re-auth: applying a restore requires the caller's password


def _require_note_if_configured(note: str | None) -> None:
    """Settings can require a documented reason on every restore apply."""
    from app.models.db import Setting
    with SessionLocal() as db:
        row = db.get(Setting, "general")
        required = bool((dict(row.value) if row else {}).get("require_restore_note"))
    if required and not (note or "").strip():
        raise HTTPException(422, "a justification for this restore is required by your "
                                 "organization's settings")


@router.post("/tenants/{tenant_id}/restore/preview")
def preview(tenant_id: int, body: RestoreIn, request: Request) -> dict:
    _require_access(request, tenant_id, body.target_tenant_id)
    _require_entitled(tenant_id, body.target_tenant_id)
    try:
        return run_restore(tenant_id, body.snapshot_ts,
                           body.selection.model_dump() if body.selection else None,
                           "dry_run", request.state.user["username"],
                           body.target_tenant_id)
    except FileNotFoundError:
        raise HTTPException(404, "snapshot not found")
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/tenants/{tenant_id}/restore/apply")
def apply(tenant_id: int, body: RestoreIn, request: Request) -> dict:
    _require_access(request, tenant_id, body.target_tenant_id)
    _require_entitled(tenant_id, body.target_tenant_id)
    _require_note_if_configured(body.note)
    from app.api.routes_backups import _require_reauth
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        _require_reauth(db, request, body.password or "", t.slug, "restore.apply")
        slug = t.slug
    from app.core import storage
    if body.snapshot_ts not in storage.list_snapshots(slug):
        raise HTTPException(404, "snapshot not found")
    # Applies run as a background job: large restores and clones take minutes,
    # which outlives any reverse-proxy timeout - and the Activity area shows
    # live progress. The report is read back from restore history when done.
    from app.core.jobs import enqueue
    jid = enqueue("config_restore", body.target_tenant_id or tenant_id,
                  request.state.user["username"],
                  params={"source_tenant_id": tenant_id,
                          "snapshot_ts": body.snapshot_ts,
                          "selection": body.selection.model_dump() if body.selection else None,
                          "actor": request.state.user["username"],
                          "target_tenant_id": body.target_tenant_id,
                          "note": body.note})
    return {"job_id": jid, "status": "queued"}


class FullDrIn(BaseModel):
    snapshot_ts: str
    note: str | None = None
    password: str | None = None
    confirm_slug: str | None = None   # must equal the tenant's slug, typed by hand
    skip_rescue: bool = False         # explicit acknowledgment for a broken current DB


@router.post("/tenants/{tenant_id}/fulldr/preflight")
def fulldr_preflight(tenant_id: int, body: FullDrIn, request: Request) -> dict:
    """Read-only target probe for the Full-DR restore modal. Global admin only -
    this feature replaces a whole database."""
    from app.core.security import require_admin
    require_admin(request)
    from app.core.fulldr import preflight
    try:
        return preflight(tenant_id, body.snapshot_ts)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))   # unreachable / wrong database


@router.post("/tenants/{tenant_id}/fulldr/apply")
def fulldr_apply(tenant_id: int, body: FullDrIn, request: Request) -> dict:
    """Queue the Full-DR restore. Admin + password reauth + typed tenant slug."""
    from app.core.security import require_admin
    require_admin(request)
    _require_entitled(tenant_id)
    _require_note_if_configured(body.note)
    from app.api.routes_backups import _require_reauth
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        _require_reauth(db, request, body.password or "", t.slug, "restore.fulldr_apply")
        slug = t.slug
    if (body.confirm_slug or "").strip() != slug:
        raise HTTPException(422, f"type the tenant slug exactly ({slug}) to confirm "
                                 "this Full-DR restore")
    from app.core import storage
    if not storage.has_dbdump(slug, body.snapshot_ts):
        raise HTTPException(404, "this snapshot has no Full-DR dump")
    from app.core.jobs import enqueue
    jid = enqueue("fulldr_restore", tenant_id, request.state.user["username"],
                  params={"snapshot_ts": body.snapshot_ts,
                          "actor": request.state.user["username"],
                          "note": body.note,
                          "skip_rescue": bool(body.skip_rescue)})
    return {"job_id": jid, "status": "queued"}


@router.get("/tenants/{tenant_id}/restore/runs")
def runs(tenant_id: int, request: Request) -> list[dict]:
    from app.core.security import require_tenant_read
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant not found")
        rows = db.query(RestoreRun).filter(RestoreRun.tenant_id == tenant_id)\
            .order_by(RestoreRun.id.desc()).limit(50).all()
        return [{"id": r.id, "snapshot_ts": r.snapshot_ts, "mode": r.mode,
                 "actor": r.actor, "note": r.note, "summary": r.summary,
                 "at": r.at.isoformat()}
                for r in rows]


@router.get("/tenants/{tenant_id}/restore/runs/{run_id}")
def run_detail(tenant_id: int, run_id: int, request: Request) -> dict:
    from app.core.security import require_tenant_read
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        r = db.get(RestoreRun, run_id)
        if r is None or r.tenant_id != tenant_id:
            raise HTTPException(404, "restore run not found")
        return {"id": r.id, "snapshot_ts": r.snapshot_ts, "mode": r.mode,
                "actor": r.actor, "note": r.note, "summary": r.summary,
                "results": r.results, "at": r.at.isoformat()}
