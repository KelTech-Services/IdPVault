"""Diff two snapshot exports: {resource_type: [objects]} -> added/removed/changed.

Objects are normalized before comparison: volatile runtime fields (usage counters)
and denormalized *_obj expansions are stripped, so drift means CONFIG drift.
"""
import json

# Runtime telemetry / server-managed noise — never config.
VOLATILE_FIELDS = {"cache_count", "verbose_name", "verbose_name_plural",
                   "id", "client_id", "created_at", "updated_at",
                   "created", "lastUpdated", "lastMembershipUpdated",
                   "_links", "_embedded"}


def normalize(obj: dict) -> dict:
    out = {}
    for k, v in obj.items():
        if k in VOLATILE_FIELDS or k.endswith("_obj"):
            continue
        out[k] = v
    return out


def _index(objs: list[dict]) -> dict:
    out = {}
    for o in objs:
        key = str(o.get("id") or o.get("pk") or o.get("client_id") or o.get("slug") or json.dumps(o, sort_keys=True))
        out[key] = o
    return out


def diff_exports(old: dict, new: dict) -> dict:
    result: dict = {}
    for rtype in sorted(set(old) | set(new)):
        a, b = _index(old.get(rtype, [])), _index(new.get(rtype, []))
        added = [b[k] for k in b.keys() - a.keys()]
        removed = [a[k] for k in a.keys() - b.keys()]
        changed = [
            {"id": k, "before": a[k], "after": b[k]}
            for k in a.keys() & b.keys()
            if json.dumps(normalize(a[k]), sort_keys=True) != json.dumps(normalize(b[k]), sort_keys=True)
        ]
        if added or removed or changed:
            result[rtype] = {"added": added, "removed": removed, "changed": changed}
    return result
