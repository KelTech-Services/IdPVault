"""Terraform HCL export (included with Business and MSP licenses).

Read-only: generates HCL from a snapshot or the live cache via the pure
engine in app/core/tfexport.py. Never writes to the provider.
"""
import io
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.core import crypto, storage, tfexport
from app.core.security import require_tenant_read
from app.models.db import SessionLocal, Tenant

router = APIRouter(tags=["terraform"])


def _require_tf_license(tenant_id: int) -> None:
    from app.core import license as lic
    if not lic.has_feature("identity") or not lic.is_tenant_entitled(tenant_id):
        raise HTTPException(402, "Terraform export is included with Business "
                                 "and MSP licenses - add one in Settings → License")


def _load_export(tenant_id: int, slug: str, key: bytes, source: str) -> dict:
    if source == "current":
        from app.core import livestate
        export = livestate.get_live_export(tenant_id)
        if not export:
            raise HTTPException(409, "live state is not loaded for this tenant yet")
        return export
    try:
        return storage.read_snapshot(slug, source, key)
    except FileNotFoundError:
        raise HTTPException(404, "snapshot not found")


def _tenant(request: Request, tenant_id: int):
    with SessionLocal() as db:
        require_tenant_read(request, db, tenant_id)
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "tenant not found")
        return (t.provider, t.slug, t.name,
                crypto.unwrap_data_key(t.wrapped_data_key))


@router.get("/tenants/{tenant_id}/terraform/object")
def tf_object(tenant_id: int, rtype: str, obj_id: str, request: Request,
              source: str = "current") -> dict:
    """One object's HCL block + import block, for the Live State modal."""
    provider, slug, _name, key = _tenant(request, tenant_id)
    _require_tf_license(tenant_id)
    export = _load_export(tenant_id, slug, key, source)
    objs = export.get(rtype)
    if objs is None:
        raise HTTPException(404, f"unknown resource type {rtype}")
    if isinstance(objs, dict):
        objs = [objs]
    for obj in objs:
        oid = tfexport.object_id(provider, rtype, obj)
        if str(oid) == obj_id or str(obj.get("pk")) == obj_id \
                or str(obj.get("id")) == obj_id:
            return tfexport.export_object(provider, rtype, obj)
    raise HTTPException(404, "object not found in this tenant's data")


class TfBundleIn(BaseModel):
    types: list
    source: str = "current"  # "current" or a snapshot timestamp


@router.post("/tenants/{tenant_id}/terraform/bundle")
def tf_bundle(tenant_id: int, body: TfBundleIn, request: Request) -> Response:
    """Zip bundle of HCL for the selected resource types."""
    provider, slug, name, key = _tenant(request, tenant_id)
    _require_tf_license(tenant_id)
    if not body.types:
        raise HTTPException(422, "select at least one resource type")
    export = _load_export(tenant_id, slug, key, body.source)
    unknown = [t for t in body.types if t not in export]
    if unknown:
        raise HTTPException(422, f"unknown resource types: {', '.join(unknown)}")
    ts = "" if body.source == "current" else body.source
    files, _report = tfexport.export_bundle(provider, export, body.types,
                                            name, ts)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fname, content in sorted(files.items()):
            z.writestr(fname, content)
    stamp = ts or "live"
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="{slug}-terraform-{stamp}.zip"'})
