"""License token verification, grace window, and gating logic — the revenue path."""
import base64
import json
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from app.core import license as lic


def _keypair():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, base64.b64encode(pub).decode()


def _mint(priv, **overrides):
    b64u = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    payload = {"customer": "Test Co", "tier": "pro", "max_tenants": None,
               "max_users": None, "features": ["identity"],
               "issued": int(time.time()),
               "expires": int(time.time()) + 86400}
    payload.update(overrides)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{b64u(body)}.{b64u(priv.sign(body))}"


def test_valid_token_verifies(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub)
    data = lic.verify(_mint(priv))
    assert data and data["customer"] == "Test Co" and data["_status"] == "active"


def test_tampered_and_garbage_rejected(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub)
    tok = _mint(priv)
    p64, s64 = tok.split(".", 1)
    evil = json.loads(base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4)))
    evil["max_tenants"] = None
    evil["customer"] = "Hacker"
    forged = base64.urlsafe_b64encode(
        json.dumps(evil, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=") + "." + s64
    assert lic.verify(forged) is None
    assert lic.verify("garbage") is None
    assert lic.verify("") is None


def test_wrong_key_rejected(monkeypatch):
    priv, _ = _keypair()
    _, other_pub = _keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", other_pub)
    assert lic.verify(_mint(priv)) is None


def test_grace_window(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub)
    in_grace = _mint(priv, expires=int(time.time()) - 86400)          # 1 day past
    data = lic.verify(in_grace)
    assert data and data["_status"] == "grace"
    past = _mint(priv, expires=int(time.time()) - (lic.GRACE_DAYS + 1) * 86400)
    assert lic.verify(past) is None


def test_perpetual(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub)
    data = lic.verify(_mint(priv, expires=None))
    assert data and data["_status"] == "active" and data["_days_left"] is None


def test_free_tier_when_no_token(monkeypatch):
    monkeypatch.setattr(lic, "_stored", lambda: {})
    info = lic.current_license()
    assert not info["valid"]
    assert info["max_tenants"] == 1 and info["max_users"] == 1
    assert info["features"] == []


def test_gating_helpers(monkeypatch):
    monkeypatch.setattr(lic, "current_license",
                        lambda: {"max_tenants": 1, "max_users": 1,
                                 "features": [], "valid": False})
    assert lic.can_add_tenant(0) is True
    assert lic.can_add_tenant(1) is False
    assert lic.has_feature("identity") is False
    monkeypatch.setattr(lic, "current_license",
                        lambda: {"max_tenants": None, "max_users": None,
                                 "features": ["identity"], "valid": True})
    assert lic.can_add_tenant(999) is True
    assert lic.has_feature("identity") is True


def test_invalid_stored_token_falls_to_free(monkeypatch):
    monkeypatch.setattr(lic, "_stored", lambda: {"token": "not.a.real.token"})
    info = lic.current_license()
    assert not info["valid"] and info.get("invalid_present") is True
    assert info["max_tenants"] == 1


# ---------- v1.3.0 activation licensing ----------
from app.core import activation as act


def test_activation_key_detection():
    assert act.is_activation_key("IDPV-JHGK-B9QF-6DGH-5GMT")
    assert act.is_activation_key("  idpv-jhgk-b9qf-6dgh-5gmt  ")   # normalized
    assert not act.is_activation_key("TFSM-AAAA-BBBB-CCCC-DDDD")   # other product
    assert not act.is_activation_key("IDPV-JHGK-B9QF-6DGH")        # short
    assert not act.is_activation_key("eyJjdXN0b21lciI.abc")        # legacy token
    assert act.norm_key(" idpv-x ") == "IDPV-X"


def test_entitlement_bound_to_instance(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub)
    monkeypatch.setattr(act, "instance_id", lambda: "AAAA-BBBB-CCCC")
    tok = _mint(priv, kind="entitlement", instance_id="AAAA-BBBB-CCCC",
                license_key="IDPV-JHGK...")
    data = lic.verify(tok)
    assert data and data["_status"] == "active" and data["license_key"] == "IDPV-JHGK..."
    # Same token on a DIFFERENT install: rejected outright.
    monkeypatch.setattr(act, "instance_id", lambda: "XXXX-YYYY-ZZZZ")
    assert lic.verify(tok) is None


def test_legacy_token_ignores_instance(monkeypatch):
    # No "kind" in the payload = legacy full key: instance id never consulted.
    priv, pub = _keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub)
    monkeypatch.setattr(act, "instance_id",
                        lambda: (_ for _ in ()).throw(AssertionError("must not be called")))
    assert lic.verify(_mint(priv)) is not None


def test_peek_is_unverified_parse_only():
    priv, _ = _keypair()
    tok = _mint(priv, kind="entitlement", instance_id="AAAA")
    p = lic.peek(tok)
    assert p and p["kind"] == "entitlement" and p["instance_id"] == "AAAA"
    assert lic.peek("garbage") is None
