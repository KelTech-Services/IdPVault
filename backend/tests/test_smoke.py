"""Smoke tests that need no DB or network."""
from app.core import crypto
from app.core.diff import diff_exports


def test_crypto_roundtrip():
    key = crypto.new_data_key()
    blob = crypto.encrypt(b"secret config", key)
    assert crypto.decrypt(blob, key) == b"secret config"
    assert blob != b"secret config"


def test_diff():
    old = {"apps": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}
    new = {"apps": [{"id": 1, "name": "a2"}, {"id": 3, "name": "c"}]}
    d = diff_exports(old, new)
    assert len(d["apps"]["added"]) == 1
    assert len(d["apps"]["removed"]) == 1
    assert d["apps"]["changed"][0]["id"] == "1"
