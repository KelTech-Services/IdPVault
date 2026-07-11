"""Diff two snapshot exports: {resource_type: [objects]} -> added/removed/changed."""
import json


def _index(objs: list[dict]) -> dict:
    out = {}
    for o in objs:
        key = str(o.get("id") or o.get("pk") or o.get("slug") or json.dumps(o, sort_keys=True))
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
            if json.dumps(a[k], sort_keys=True) != json.dumps(b[k], sort_keys=True)
        ]
        if added or removed or changed:
            result[rtype] = {"added": added, "removed": removed, "changed": changed}
    return result
