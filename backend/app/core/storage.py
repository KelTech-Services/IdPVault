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


# Path-safety: slugs and timestamps come from API input and become path
# components. Validate their exact shape so traversal is structurally impossible.
import re
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_TS_RE = re.compile(r"^\d{8}T\d{6}Z$")


def _safe_slug(slug: str) -> str:
    if not _SLUG_RE.fullmatch(slug or ""):
        raise ValueError("invalid tenant slug")
    return slug


def _safe_ts(ts: str) -> str:
    if not _TS_RE.fullmatch(ts or ""):
        raise ValueError("invalid snapshot timestamp")
    return ts


def _contained(path: str) -> str:
    """Resolve and require the path to stay inside the data dir (anti-traversal)."""
    base = os.path.realpath(get_settings().data_dir)
    full = os.path.realpath(path)
    if not full.startswith(base + os.sep):
        raise ValueError("path escapes the data directory")
    return full


def snapshot_dir(tenant_slug: str, ts: str) -> str:
    return _contained(os.path.join(get_settings().data_dir,
                                   _safe_slug(tenant_slug), _safe_ts(ts)))


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


def update_manifest(tenant_slug: str, ts: str, extra: dict) -> None:
    """Merge keys into an existing snapshot manifest on disk. Used for facts
    only known after the snapshot is written (e.g. the Full-DR dump outcome) -
    without this, post-write mutations exist only in memory and the UI never
    sees them."""
    m = read_manifest(tenant_slug, ts)
    if m is None:
        return
    m.update(extra)
    base = os.path.realpath(get_settings().data_dir)
    path = os.path.normpath(os.path.join(
        base, _safe_slug(tenant_slug), _safe_ts(ts), "manifest.json"))
    if not path.startswith(base + os.sep):
        return
    try:
        with open(path, "w") as f:
            json.dump(m, f, indent=2)
    except OSError:
        pass   # metadata only; the snapshot itself is intact


def read_snapshot(tenant_slug: str, ts: str, data_key: bytes) -> dict:
    base = os.path.realpath(get_settings().data_dir)
    path = os.path.normpath(os.path.join(
        base, _safe_slug(tenant_slug), _safe_ts(ts), "objects.json.enc"))
    if not path.startswith(base + os.sep):
        raise ValueError("path escapes the data directory")
    with open(path, "rb") as f:
        return json.loads(crypto.decrypt(f.read(), data_key))


def list_snapshots(tenant_slug: str) -> list[str]:
    base = os.path.join(get_settings().data_dir, _safe_slug(tenant_slug))
    if not os.path.isdir(base):
        return []
    # Only real config snapshots (dirs holding an objects.json.enc payload). This
    # deliberately excludes the sibling `identities/` dir, which would otherwise be
    # treated as a snapshot and break drift detection + retention.
    return sorted(d for d in os.listdir(base)
                  if os.path.isfile(os.path.join(base, d, "objects.json.enc")))


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


def dbdump_size(tenant_slug: str, ts: str) -> int:
    try:
        return os.path.getsize(os.path.join(snapshot_dir(tenant_slug, ts), "pgdump.sql.enc"))
    except Exception:   # no dump, bad slug/ts (e.g. deleted tenant) -> just 0
        return 0


def read_dbdump(tenant_slug: str, ts: str, data_key: bytes) -> bytes:
    """Decrypt a snapshot's Full-DR dump back to plain SQL."""
    from app.core import crypto
    with open(os.path.join(snapshot_dir(tenant_slug, ts), "pgdump.sql.enc"), "rb") as f:
        return crypto.decrypt(f.read(), data_key)


def write_rescue_dump(tenant_slug: str, data_key: bytes, dump: bytes) -> str:
    """Encrypted pre-restore rescue dump of the CURRENT database, taken right
    before a Full-DR restore replaces it. Lives under <slug>/rescue/ (its own
    dir, so snapshot listing/pruning never touch it). Returns the file path."""
    from app.core import crypto
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = _contained(os.path.join(get_settings().data_dir, _safe_slug(tenant_slug), "rescue"))
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"{ts}.sql.enc")
    with open(path, "wb") as f:
        f.write(crypto.encrypt(dump, data_key))
    return path


def identity_dir(tenant_slug: str, ts: str) -> str:
    return _contained(os.path.join(get_settings().data_dir, _safe_slug(tenant_slug),
                                   "identities", _safe_ts(ts)))


def write_identities(tenant_slug: str, data_key: bytes, payload: dict) -> dict:
    from app.core import crypto
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = identity_dir(tenant_slug, ts)
    os.makedirs(path, exist_ok=True)
    raw = json.dumps(payload, sort_keys=True).encode()
    blob = crypto.encrypt(raw, data_key)
    with open(os.path.join(path, "identities.json.enc"), "wb") as f:
        f.write(blob)
    manifest = {"timestamp": ts,
                "counts": {k: len(v) for k, v in payload.items() if isinstance(v, list)},
                "sha256_plain": hashlib.sha256(raw).hexdigest(),
                "size_encrypted": len(blob)}
    with open(os.path.join(path, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def read_identities(tenant_slug: str, ts: str, data_key: bytes) -> dict:
    from app.core import crypto
    base = os.path.realpath(get_settings().data_dir)
    path = os.path.normpath(os.path.join(
        base, _safe_slug(tenant_slug), "identities", _safe_ts(ts), "identities.json.enc"))
    if not path.startswith(base + os.sep):
        raise ValueError("path escapes the data directory")
    with open(path, "rb") as f:
        return json.loads(crypto.decrypt(f.read(), data_key))


def list_identity_snapshots(tenant_slug: str) -> list[str]:
    base = os.path.join(get_settings().data_dir, _safe_slug(tenant_slug), "identities")
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))


def prune_identities(tenant_slug: str, keep: int) -> list[str]:
    snaps = list_identity_snapshots(tenant_slug)
    doomed = snaps[:-keep] if keep and len(snaps) > keep else []
    for ts in doomed:
        shutil.rmtree(identity_dir(tenant_slug, ts), ignore_errors=True)
    return doomed


def read_manifest(tenant_slug: str, ts: str) -> dict | None:
    """Plaintext manifest for a snapshot, or None if missing/corrupt."""
    base = os.path.realpath(get_settings().data_dir)
    path = os.path.normpath(os.path.join(
        base, _safe_slug(tenant_slug), _safe_ts(ts), "manifest.json"))
    if not path.startswith(base + os.sep):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


def read_changes_cache(tenant_slug: str, ts: str) -> dict | None:
    base = os.path.realpath(get_settings().data_dir)
    path = os.path.normpath(os.path.join(
        base, _safe_slug(tenant_slug), _safe_ts(ts), "changes.json"))
    if not path.startswith(base + os.sep):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


def write_changes_cache(tenant_slug: str, ts: str, data: dict) -> None:
    base = os.path.realpath(get_settings().data_dir)
    path = os.path.normpath(os.path.join(
        base, _safe_slug(tenant_slug), _safe_ts(ts), "changes.json"))
    if not path.startswith(base + os.sep):
        return
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except OSError:
        pass   # cache is best-effort; the diff recomputes next time


def delete_snapshot(tenant_slug: str, ts: str) -> None:
    """Remove a config snapshot dir (objects, manifest, dump, caches)."""
    base = os.path.realpath(get_settings().data_dir)
    path = os.path.normpath(os.path.join(
        base, _safe_slug(tenant_slug), _safe_ts(ts)))
    if not path.startswith(base + os.sep):
        return
    shutil.rmtree(path, ignore_errors=True)


def delete_identity_snapshot(tenant_slug: str, ts: str) -> None:
    base = os.path.realpath(get_settings().data_dir)
    path = os.path.normpath(os.path.join(
        base, _safe_slug(tenant_slug), "identities", _safe_ts(ts)))
    if not path.startswith(base + os.sep):
        return
    shutil.rmtree(path, ignore_errors=True)
