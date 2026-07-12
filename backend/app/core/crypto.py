"""Envelope encryption: master key (file mount) wraps per-tenant data keys.

- master.key: 32 random bytes, mounted read-only, never leaves the host.
- Each tenant gets a random 32-byte data key, stored AES-GCM-wrapped in the DB.
- Snapshots and IdP credentials are encrypted with the tenant data key.
"""
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_NONCE = 12


def _master_key() -> bytes:
    path = get_settings().master_key_file
    with open(path, "rb") as f:
        key = f.read().strip()
    if len(key) != 32:
        raise RuntimeError(f"master key at {path} must be exactly 32 bytes")
    return key


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    nonce = os.urandom(_NONCE)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def decrypt(blob: bytes, key: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[:_NONCE], blob[_NONCE:], None)


def new_data_key() -> bytes:
    return os.urandom(32)


def wrap_data_key(data_key: bytes) -> bytes:
    return encrypt(data_key, _master_key())


def verify_master_key_matches_db() -> None:
    """Fail fast at boot if the mounted master key can't decrypt existing data.

    A wrong (e.g. regenerated) key with an existing database would otherwise
    surface later as scattered decryption failures; refuse to start instead."""
    from app.models.db import SessionLocal, Tenant
    with SessionLocal() as db:
        t = db.query(Tenant).first()
    if t is None:
        return
    try:
        unwrap_data_key(t.wrapped_data_key)
    except Exception:
        raise SystemExit(
            "FATAL: the master key does not match this database's encrypted data "
            "(wrong or regenerated master.key). Restore the original key backup - "
            "IdPVault refuses to start to protect existing snapshots and credentials.")


def unwrap_data_key(wrapped: bytes) -> bytes:
    return decrypt(wrapped, _master_key())
