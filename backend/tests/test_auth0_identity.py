"""Auth0 Users & Access mapping - no network, pure shape tests."""
from app.providers.auth0 import Auth0Adapter


def test_slim_user_maps_auth0_fields_to_identity_shape():
    u = {"user_id": "auth0|abc123", "email": "jane@acme.com", "name": "Jane Doe",
         "username": "jdoe", "blocked": False, "created_at": "2026-01-01T00:00:00Z",
         "identities": [{"connection": "Username-Password-Authentication"}],
         "last_login": "2026-07-01T00:00:00Z", "logins_count": 42}
    s = Auth0Adapter._slim_user(u)
    assert s["id"] == "auth0|abc123"
    assert s["profile"]["login"] == "jane@acme.com"     # natural key = email
    assert s["profile"]["username"] == "jdoe"
    assert s["status"] == "ACTIVE"
    assert s["connection"] == "Username-Password-Authentication"
    assert "logins_count" not in s and "last_login" not in s  # volatile fields excluded


def test_slim_user_blocked_and_fallback_login():
    u = {"user_id": "auth0|x", "blocked": True, "identities": []}
    s = Auth0Adapter._slim_user(u)
    assert s["status"] == "BLOCKED"
    assert s["profile"]["login"] == "auth0|x"  # no email/username -> id fallback
    assert s["connection"] is None


def test_supports_identity_flags():
    from app.providers import identity_supported
    assert identity_supported("auth0") is True
    assert identity_supported("okta") is True
    assert identity_supported("authentik") is True
    assert identity_supported("nope") is False
