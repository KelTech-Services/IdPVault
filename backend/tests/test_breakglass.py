"""SSO enforcement and the break-glass guardrails.

Ported from StackMerger. The whole point of these rules is that turning SSO
on - or a directory mistake afterwards - must never be able to lock an admin
out of their own install. Every rule here protects against a mistake, not an
attacker.
"""
import pytest
from fastapi import HTTPException

from app.api import routes_users as ru
from app.core import oidc


class _U:
    def __init__(self, uid, role="admin", breakglass=None, external=None,
                 mfa_enabled=False, is_active=True, username=None):
        self.id, self.role = uid, role
        self.breakglass, self.external = breakglass, external
        self.mfa_enabled, self.is_active = mfa_enabled, is_active
        self.username = username or f"u{uid}"


class _All:
    def __init__(self, users): self.users = users
    def all(self): return self.users


class _DB:
    def __init__(self, users): self.users = users
    def query(self, *a, **k): return _All(self.users)


# ---------- who still gets the password form ----------

@pytest.mark.parametrize("mode,bg,ext,allowed", [
    ("off", None, None, True),
    ("optional", None, None, True),
    ("required", None, None, False),   # ordinary staff: SSO only
    ("required", True, None, True),    # break-glass admin: the way back in
    ("required", None, True, True),    # external client contact: no SSO identity
    ("required", True, True, True),
])
def test_password_login_allowed(mode, bg, ext, allowed):
    u = _U(1, breakglass=bg, external=ext)
    assert oidc.password_login_allowed(u, mode) is allowed


# ---------- counting the way back in ----------

def test_breakglass_admin_must_have_mfa_to_count():
    # A break-glass flag without MFA is not a way back in, so it does not count.
    db = _DB([_U(1, breakglass=True, mfa_enabled=True),
              _U(2, breakglass=True, mfa_enabled=False),
              _U(3, breakglass=None, mfa_enabled=True)])
    assert ru._other_breakglass_admins(db, than_user_id=99) == 1
    assert ru._other_breakglass_admins(db, than_user_id=1) == 0


def test_disabled_or_non_admin_breakglass_does_not_count():
    db = _DB([_U(1, breakglass=True, mfa_enabled=True, is_active=False),
              _U(2, role="user", breakglass=True, mfa_enabled=True)])
    assert ru._other_breakglass_admins(db, than_user_id=99) == 0


def test_other_active_admins_excludes_self_and_disabled():
    db = _DB([_U(1), _U(2), _U(3, is_active=False), _U(4, role="user")])
    assert ru._other_active_admins(db, than_user_id=1) == 1


# ---------- the guard itself ----------

def _guard(users, target, mode, monkeypatch):
    monkeypatch.setattr(oidc, "mode", lambda: mode)
    return ru._guard_last_breakglass(_DB(users), target)


def test_guard_blocks_removing_the_last_breakglass_admin(monkeypatch):
    last = _U(1, breakglass=True, mfa_enabled=True)
    with pytest.raises(HTTPException) as e:
        _guard([last], last, "required", monkeypatch)
    assert e.value.status_code == 422
    assert "break-glass" in e.value.detail


def test_guard_allows_it_when_another_qualifying_admin_remains(monkeypatch):
    last = _U(1, breakglass=True, mfa_enabled=True)
    spare = _U(2, breakglass=True, mfa_enabled=True)
    _guard([last, spare], last, "required", monkeypatch)


def test_guard_is_inert_when_sso_is_not_required(monkeypatch):
    last = _U(1, breakglass=True, mfa_enabled=True)
    _guard([last], last, "optional", monkeypatch)
    _guard([last], last, "off", monkeypatch)


def test_guard_ignores_users_that_are_not_breakglass(monkeypatch):
    plain = _U(1, breakglass=None, mfa_enabled=True)
    _guard([plain], plain, "required", monkeypatch)
