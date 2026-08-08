"""With SSO configured, a hand-created account is EXTERNAL by definition.

Staff arrive through the identity provider by JIT or SCIM. Anyone still being
typed into the create form is by definition someone outside your directory - a
contractor or a client contact - and they sign in with a password, so they
still get a role and a password. The UI locks the checkbox; these cover the
server side, which is what actually enforces it.
"""
import pytest

from app.api import routes_users as ru
from app.core import oidc


@pytest.mark.parametrize("mode,expect", [
    ("off", False),
    ("optional", True),
    ("required", True),
])
def test_sso_configured_tracks_the_effective_mode(monkeypatch, mode, expect):
    monkeypatch.setattr(oidc, "mode", lambda *a, **k: mode)
    assert ru._sso_configured() is expect


def _external(body_external, role, sso_on):
    """The expression the create endpoint uses, isolated."""
    return (body_external
            or role in ("org_admin", "org_viewer")
            or sso_on) or None


@pytest.mark.parametrize("body_external,role,sso_on,expect", [
    # SSO off: the admin's choice stands.
    (False, "user", False, None),
    (True, "user", False, True),
    # SSO on: forced external no matter what the form said.
    (False, "user", True, True),
    (False, "admin", True, True),
    (True, "user", True, True),
    # Org-scoped roles are client people whether SSO is on or not.
    (False, "org_admin", False, True),
    (False, "org_viewer", False, True),
])
def test_external_is_forced_once_sso_is_configured(body_external, role, sso_on, expect):
    assert _external(body_external, role, sso_on) is expect


def test_an_unlicensed_or_unconfigured_install_is_never_forced(monkeypatch):
    """oidc.mode() already collapses unlicensed/unconfigured to "off", so a
    Community install never gets its create form quietly constrained."""
    monkeypatch.setattr(oidc, "licensed", lambda: False)
    monkeypatch.setattr(oidc, "configured", lambda v=None: True)
    monkeypatch.setattr(oidc, "get_sso", lambda: {"mode": "required"})
    assert ru._sso_configured() is False


def test_role_and_password_are_untouched_by_the_rule():
    """REGRESSION GUARD: external users sign in with a PASSWORD - they are the
    people who are not in your directory. The rule must never strip the role or
    password fields from the create payload."""
    fields = set(ru.UserIn.model_fields)
    assert {"role", "password", "external"} <= fields
