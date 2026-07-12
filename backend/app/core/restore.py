"""Restore engine: plan (dry-run) and apply, dependency-ordered, per-object reporting.

Dry-run is provider-agnostic: it compares snapshot objects against a fresh live
export. Apply pushes objects back through the provider adapter. Restore ordering and
the never-restore set come from the adapter; adapters without push_object land in the
report as unsupported.
"""
import json

from app.core import crypto, storage
from app.core.diff import normalize
from app.core.events import _name as obj_name
from app.models.db import AuditLog, RestoreRun, SessionLocal, Tenant
from app.providers import get_adapter

def _order_key(rtype: str, order: list):
    return (order.index(rtype), "") if rtype in order else (len(order), rtype)


def _selected(selection: dict | None, rtype: str, oid: str) -> bool:
    if not selection:
        return True
    rtypes = selection.get("resource_types")
    objects = selection.get("objects")
    if objects:
        return any(o.get("resource_type") == rtype and str(o.get("object_id")) == oid
                   for o in objects)
    if rtypes:
        return rtype in rtypes
    return True


def build_plan(snap_export: dict, live_export: dict, selection: dict | None,
               adapter) -> list[dict]:
    items = []
    for rtype in sorted(snap_export.keys(), key=lambda rt: _order_key(rt, adapter.restore_order)):
        # never_restore types stay VISIBLE when they differ from live (so a deleted
        # app is seen, marked unsupported) — they're just not auto-restorable.
        if rtype in adapter.derived_types:
            continue  # server-regenerated side-effects — never restore work
        unsupported = rtype in adapter.never_restore
        live_idx = {adapter.natural_key(rtype, o): o for o in live_export.get(rtype, [])}
        for obj in snap_export.get(rtype, []):
            key = adapter.natural_key(rtype, obj)
            if not _selected(selection, rtype, key):
                continue
            live = live_idx.get(key)
            if live is None:
                action, fields = "create", []
            else:
                n_obj, n_live = normalize(obj), normalize(live)
                fields = sorted(k for k in set(n_obj) | set(n_live)
                                if json.dumps(n_obj.get(k), sort_keys=True, default=str)
                                != json.dumps(n_live.get(k), sort_keys=True, default=str))
                action = "update" if fields else "identical"
            if unsupported and action == "identical":
                continue  # unchanged unsupported objects would just be noise
            items.append({"resource_type": rtype, "object_id": key,
                          "object_name": obj_name(obj), "action": action,
                          "changed_fields": fields[:30],
                          "managed": bool(obj.get("managed")),
                          "restorable": not unsupported,
                          "_obj": obj, "_live": live})
    return items


def run_restore(tenant_id: int, snapshot_ts: str, selection: dict | None,
                mode: str, actor: str, target_tenant_id: int | None = None) -> dict:
    """Same-tenant restore, or clone/promote when target_tenant_id points at a
    different tenant of the SAME provider: source snapshot -> target tenant."""
    assert mode in ("dry_run", "apply")
    with SessionLocal() as db:
        src = db.get(Tenant, tenant_id)
        if src is None:
            raise ValueError("tenant not found")
        t = db.get(Tenant, target_tenant_id) if target_tenant_id else src
        if t is None:
            raise ValueError("target tenant not found")
        if t.provider != src.provider:
            raise ValueError(f"provider mismatch: {src.provider} snapshot cannot be "
                             f"applied to a {t.provider} tenant")
        src_key = crypto.unwrap_data_key(src.wrapped_data_key)
        data_key = crypto.unwrap_data_key(t.wrapped_data_key)
        creds = crypto.decrypt(t.enc_credentials, data_key).decode()
        adapter = get_adapter(t.provider, t.base_url, creds)

        snap = storage.read_snapshot(src.slug, snapshot_ts, src_key)
        live = adapter.export()
        adapter.begin_restore(snap, live)   # adapters build id-remap state here
        plan = build_plan(snap, live, selection, adapter)

        for item in plan:
            obj = item.pop("_obj")
            live_obj = item.pop("_live")
            if not item.get("restorable", True):
                item["status"] = "unsupported"
                item["error"] = f"{item['resource_type']}: not auto-restorable yet"
                continue
            if mode == "dry_run":
                item["status"] = "planned" if item["action"] != "identical" else "skipped"
                continue
            if item["action"] == "identical":
                item["status"] = "skipped"
                continue
            if item["managed"]:
                item["status"] = "skipped_managed"
                continue
            try:
                pushed, live_pk = adapter.push_object(item["resource_type"], obj, live_obj)
                item["status"] = pushed
                item["live_pk"] = live_pk
            except NotImplementedError as e:
                item["status"] = "unsupported"
                item["error"] = str(e)
            except Exception as e:
                item["status"] = "failed"
                item["error"] = str(e)[:300]

        summary: dict = {"mode": mode, "total": len(plan)}
        if t.id != src.id:
            summary["promote"] = {"source": src.slug, "target": t.slug}
        for it in plan:
            for key, plural in (("action", "actions"), ("status", "statuses")):
                summary.setdefault(plural, {})
                summary[plural][it[key]] = summary[plural].get(it[key], 0) + 1

        run = RestoreRun(tenant_id=t.id, snapshot_ts=snapshot_ts, mode=mode, actor=actor,
                         summary=summary, results={"items": plan})
        db.add(run)
        db.add(AuditLog(actor=actor, action=f"restore.{mode}",
                        detail={"tenant": t.slug, "source": src.slug,
                                "snapshot": snapshot_ts, "total": len(plan)}))
        db.commit()
        if mode == "apply":
            from app.core.alerts import alert_restore
            alert_restore(t.name, "config", summary)
        return {"restore_run_id": run.id, "summary": summary, "items": plan}
