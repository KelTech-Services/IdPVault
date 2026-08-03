"""SCIM role mapping and the brick-proof guards.

The rules under test, all of which protect an install from a directory
mistake rather than from an attacker:
  - highest mapped role wins across a user's push groups
  - leaving every mapped group drops you to the SSO default role
  - break-glass accounts are never re-roled by the directory
  - MSP org-scoped users are never touched by the directory
  - the last active administrator is never demoted or deactivated
  - every skip is audited, so the reason is visible
"""
from app.api import routes_scim as scim


class _U:
    def __init__(self, uid, role="user", breakglass=None, org_id=None,
                 is_active=True, username=None):
        self.id, self.role, self.breakglass = uid, role, breakglass
        self.org_id, self.is_active = org_id, is_active
        self.username = username or f"u{uid}"


class _Count:
    def __init__(self, n): self._n = n
    def filter(self, *a, **k): return self
    def count(self): return self._n


class _RoleRows:
    def __init__(self, roles): self.roles = roles
    def join(self, *a, **k): return self
    def filter(self, *a, **k): return self
    def all(self): return [(r,) for r in self.roles]


class _DB:
    """Serves the two queries recompute makes: mapped roles, and the count of
    OTHER active admins."""
    def __init__(self, users, roles, other_admins=1):
        self.users = {u.id: u for u in users}
        self.roles, self.other_admins = roles, other_admins
        self.audits = []

    def get(self, _model, uid): return self.users.get(uid)

    def query(self, arg):
        from app.models.db import User
        if arg is User:
            return _Count(self.other_admins)
        return _RoleRows(self.roles)

    def add(self, row): self.audits.append(row)


def _run(db, uid, monkeypatch, default="user"):
    monkeypatch.setattr(scim.oidc, "jit_role", lambda: default)
    scim.recompute_scim_roles(db, [uid])


def _reasons(db):
    return [a.detail.get("reason") for a in db.audits
            if a.action == "scim.role_skipped"]


def test_highest_mapped_role_wins(monkeypatch):
    u = _U(1, role="user")
    db = _DB([u], ["user", "admin"], other_admins=1)
    _run(db, 1, monkeypatch)
    assert u.role == "admin"


def test_leaving_every_mapped_group_drops_to_the_default_role(monkeypatch):
    u = _U(1, role="admin")
    db = _DB([u], [], other_admins=2)      # no mapped groups left
    _run(db, 1, monkeypatch, default="user")
    assert u.role == "user"


def test_break_glass_accounts_are_never_re_roled(monkeypatch):
    """Break-glass is the emergency door. If the directory could demote it,
    a bad group change could lock the install out entirely."""
    u = _U(1, role="admin", breakglass=True)
    db = _DB([u], [], other_admins=5)
    _run(db, 1, monkeypatch, default="user")
    assert u.role == "admin"
    assert "break-glass account" in _reasons(db)


def test_msp_org_scoped_users_are_never_touched(monkeypatch):
    """An MSP's clients are not in the MSP's staff directory. SCIM must not
    reshape their roles - that is MSP scoping, not directory membership."""
    u = _U(1, role="org_admin", org_id=7)
    db = _DB([u], ["admin"], other_admins=3)
    _run(db, 1, monkeypatch)
    assert u.role == "org_admin"
    assert "org-scoped user" in _reasons(db)


def test_last_active_administrator_is_never_demoted(monkeypatch):
    u = _U(1, role="admin")
    db = _DB([u], [], other_admins=0)      # nobody else is an active admin
    _run(db, 1, monkeypatch, default="user")
    assert u.role == "admin"
    assert "last active administrator" in _reasons(db)


def test_demotion_is_allowed_when_another_admin_remains(monkeypatch):
    u = _U(1, role="admin")
    db = _DB([u], [], other_admins=1)
    _run(db, 1, monkeypatch, default="user")
    assert u.role == "user"
    assert _reasons(db) == []


def test_only_global_roles_are_rankable():
    """org_admin/org_viewer must not be reachable through group mapping -
    they need an org_id the directory cannot supply."""
    assert set(scim._ROLE_RANK) == {"user", "admin"}


def test_scim_token_has_the_product_prefix_and_is_hashed():
    tok, digest = scim.new_token()
    assert tok.startswith("idpv_scim_")
    import hashlib
    assert digest == hashlib.sha256(tok.encode()).hexdigest()
    # Two calls never collide.
    assert scim.new_token()[0] != tok


def test_eq_filter_parsing():
    assert scim._eq_filter('userName eq "eric@keltech.services"') \
        == "eric@keltech.services"
    assert scim._eq_filter("userName eq 'a@b.com'") == "a@b.com"
    assert scim._eq_filter("") is None
    assert scim._eq_filter("userName sw \"eric\"") is None
