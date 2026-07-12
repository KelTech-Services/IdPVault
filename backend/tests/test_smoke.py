"""Crypto + diff smoke tests — no DB or network."""
import pytest
from app.core import crypto
from app.core.diff import diff_exports, normalize


def test_crypto_roundtrip():
    key = crypto.new_data_key()
    blob = crypto.encrypt(b"secret config", key)
    assert crypto.decrypt(blob, key) == b"secret config"
    assert blob != b"secret config"


def test_crypto_tamper_rejected():
    key = crypto.new_data_key()
    blob = bytearray(crypto.encrypt(b"payload", key))
    blob[-1] ^= 0xFF
    with pytest.raises(Exception):
        crypto.decrypt(bytes(blob), key)


def test_envelope_wrap_unwrap():
    dk = crypto.new_data_key()
    assert crypto.unwrap_data_key(crypto.wrap_data_key(dk)) == dk


def test_diff_add_remove_change():
    old = {"apps": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}
    new = {"apps": [{"id": 1, "name": "a2"}, {"id": 3, "name": "c"}]}
    d = diff_exports(old, new)
    assert len(d["apps"]["added"]) == 1
    assert len(d["apps"]["removed"]) == 1
    assert d["apps"]["changed"][0]["id"] == "1"


def test_volatile_fields_not_config():
    # server ids/timestamps must never count as configuration drift
    a = {"g": [{"id": "old", "name": "x", "lastUpdated": "1", "created": "1"}]}
    b = {"g": [{"id": "old", "name": "x", "lastUpdated": "2", "created": "2"}]}
    assert diff_exports(a, b) == {}
    n = normalize({"pk": 5, "id": 9, "client_id": "c", "name": "keep",
                   "assigned_application_slug": "x", "_links": {}})
    assert n == {"name": "keep"}
