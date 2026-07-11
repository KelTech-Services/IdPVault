"""Turn a snapshot diff into Event rows — the per-object change feed."""
import json


def _name(obj: dict) -> str:
    for k in ("name", "label", "displayName", "display_name", "slug", "username", "title"):
        v = obj.get(k)
        if isinstance(v, str) and v:
            return v[:250]
        if isinstance(v, dict):  # okta apps: label at top; policies: name
            continue
    prof = obj.get("profile") or {}
    if isinstance(prof, dict) and prof.get("name"):
        return str(prof["name"])[:250]
    return ""


def _id(obj: dict) -> str:
    for k in ("pk", "id", "slug", "brand_uuid"):
        if obj.get(k) is not None:
            return str(obj[k])[:110]
    return ""


def _changed_fields(before: dict, after: dict) -> list[str]:
    keys = set(before) | set(after)
    return sorted(k for k in keys
                  if json.dumps(before.get(k), sort_keys=True, default=str)
                  != json.dumps(after.get(k), sort_keys=True, default=str))[:25]


def extract_events(tenant_id: int, snapshot_ts: str, diff: dict) -> list:
    """diff = output of diff_exports(); returns unsaved Event ORM rows."""
    from app.models.db import Event
    rows = []
    for rtype, changes in (diff or {}).items():
        for obj in changes.get("added", []):
            rows.append(Event(tenant_id=tenant_id, snapshot_ts=snapshot_ts, event_type="add",
                              resource_type=rtype, object_id=_id(obj), object_name=_name(obj),
                              detail={}))
        for obj in changes.get("removed", []):
            rows.append(Event(tenant_id=tenant_id, snapshot_ts=snapshot_ts, event_type="delete",
                              resource_type=rtype, object_id=_id(obj), object_name=_name(obj),
                              detail={}))
        for ch in changes.get("changed", []):
            before, after = ch.get("before", {}), ch.get("after", {})
            rows.append(Event(tenant_id=tenant_id, snapshot_ts=snapshot_ts, event_type="update",
                              resource_type=rtype, object_id=str(ch.get("id", ""))[:110],
                              object_name=_name(after) or _name(before),
                              detail={"fields": _changed_fields(before, after)}))
    return rows
