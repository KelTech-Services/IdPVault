"""Snapshot storage on disk.

Layout: <data_dir>/<tenant_slug>/<YYYYMMDDTHHMMSSZ>/
    objects.json.enc   -- encrypted export payload
    manifest.json      -- plaintext metadata: counts per resource type, sizes, hash
"""
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone

from app.config import get_settings
from app.core import crypto


def snapshot_dir(tenant_slug: str, ts: str) -> str:
    return os.path.join(get_settings().data_dir, tenant_slug, ts)


def write_snapshot(tenant_slug: str, data_key: bytes, export: dict) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = snapshot_dir(tenant_slug, ts)
    os.makedirs(path, exist_ok=True)

    raw = json.dumps(export, sort_keys=True).encode()
    blob = crypto.encrypt(raw, data_key)
    with open(os.path.join(path, "objects.json.enc"), "wb") as f:
        f.write(blob)

    manifest = {
        "timestamp": ts,
        "counts": {k: len(v) for k, v in export.items()},
        "sha256_plain": hashlib.sha256(raw).hexdigest(),
        "size_encrypted": len(blob),
    }
    with open(os.path.join(path, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def read_snapshot(tenant_slug: str, ts: str, data_key: bytes) -> dict:
    with open(os.path.join(snapshot_dir(tenant_slug, ts), "objects.json.enc"), "rb") as f:
        return json.loads(crypto.decrypt(f.read(), data_key))


def list_snapshots(tenant_slug: str) -> list[str]:
    base = os.path.join(get_settings().data_dir, tenant_slug)
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))


def prune(tenant_slug: str, keep: int) -> list[str]:
    """Delete oldest snapshots beyond `keep`. Returns removed timestamps."""
    snaps = list_snapshots(tenant_slug)
    doomed = snaps[:-keep] if keep and len(snaps) > keep else []
    for ts in doomed:
        shutil.rmtree(snapshot_dir(tenant_slug, ts), ignore_errors=True)
    return doomed


def write_dbdump(tenant_slug: str, ts: str, data_key: bytes, dump: bytes) -> int:
    from app.core import crypto
    blob = crypto.encrypt(dump, data_key)
    with open(os.path.join(snapshot_dir(tenant_slug, ts), "pgdump.sql.enc"), "wb") as f:
        f.write(blob)
    return len(blob)


def has_dbdump(tenant_slug: str, ts: str) -> bool:
    return os.path.exists(os.path.join(snapshot_dir(tenant_slug, ts), "pgdump.sql.enc"))
