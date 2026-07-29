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
                product="idpvault", license_key="IDPV-JHGK...")
    data = lic.verify(tok)
    assert data and data["_status"] == "active" and data["license_key"] == "IDPV-JHGK..."
    # Same token on a DIFFERENT install: rejected outright.
    monkeypatch.setattr(act, "instance_id", lambda: "XXXX-YYYY-ZZZZ")
    assert lic.verify(tok) is None


def test_entitlement_product_stamped(monkeypatch):
    # One signing key serves every KelTech app - a TFsmith entitlement must
    # NEVER unlock IdPVault (and vice versa).
    priv, pub = _keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub)
    monkeypatch.setattr(act, "instance_id", lambda: "AAAA-BBBB-CCCC")
    wrong = _mint(priv, kind="entitlement", instance_id="AAAA-BBBB-CCCC",
                  product="tfsmith")
    assert lic.verify(wrong) is None
    unstamped = _mint(priv, kind="entitlement", instance_id="AAAA-BBBB-CCCC")
    assert lic.verify(unstamped) is None


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


# --- one unreadable resource type must not fail a whole backup (7/29) ---

def test_unavailable_never_reaches_a_snapshot_diff_or_restore():
    """REGRESSION GUARD: an Okta org without API Access Management 401s on
    /api/v1/authorizationServers. Adapters record that instead of aborting the
    export, but the marker must NEVER live inside a stored snapshot - there it
    would diff as drift, fire an alert, and be handed to a restore plan as if
    it were a resource type."""
    import httpx

    from app.providers.base import (UNAVAILABLE_KEY, describe_unavailable,
                                    record_unavailable, split_unavailable)

    r = httpx.Response(401, json={
        "errorCode": "E0000015",
        "errorSummary": "You do not have permission to access the feature"})
    msg = describe_unavailable(r)
    assert "E0000015" in msg and "not licensed" in msg

    export = {"apps": [{"id": "a"}], "groups": []}
    record_unavailable(export, "authorization_servers", r)
    assert export[UNAVAILABLE_KEY]                      # recorded on the export
    assert export["authorization_servers"] == []

    clean, unavailable = split_unavailable(export)
    assert UNAVAILABLE_KEY not in clean                 # stripped for storage
    assert not any(k.startswith("_") for k in clean)
    assert set(clean) == {"apps", "groups", "authorization_servers"}
    assert unavailable["authorization_servers"].startswith("HTTP 401")
    # the original is untouched, so callers that want the reason still have it
    assert UNAVAILABLE_KEY in export


def test_split_unavailable_is_safe_on_a_normal_export():
    from app.providers.base import split_unavailable
    e = {"apps": [{"id": 1}], "groups": [{"id": 2}]}
    clean, un = split_unavailable(e)
    assert clean == e and un == {}


def test_diff_does_not_see_a_stripped_marker():
    """A live export with the marker, diffed against a stored snapshot without
    it, must not report a phantom added resource type."""
    import httpx

    from app.core.diff import diff_exports
    from app.providers.base import record_unavailable, split_unavailable

    stored = {"apps": [{"id": "a"}]}
    live = {"apps": [{"id": "a"}]}
    record_unavailable(live, "authorization_servers", httpx.Response(403))
    live_clean, _ = split_unavailable(live)
    d = diff_exports(stored, live_clean)
    for rt, ch in d.items():
        assert rt != "_unavailable"
        assert not ch["added"], f"phantom drift on {rt}"
