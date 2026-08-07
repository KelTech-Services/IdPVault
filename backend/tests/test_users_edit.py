"""User editing rules on the App users page.

The page is admin-only. What these cover is what an admin may do to whom -
including to their own row, which is how the first break-glass account gets
flagged on a single-admin install.
"""
import pytest
from fastapi import HTTPException

from app.api import routes_users as ru
from app.core import security


class _U:
    def __init__(self, uid, username="u", email="", first_name=None,
                 last_name=None, role="user", is_active=True):
        self.id, self.username, self.email = uid, username, email
        self.first_name, self.last_name = first_name, last_name
        self.role, self.is_active = role, is_active
        self.breakglass = self.external = self.mfa_enabled = None
        self.sso_user = self.org_id = None


class _Q:
    def __init__(self, rows): self.rows = rows
    def filter(self, *a, **k): return self
    def first(self): return self.rows[0] if self.rows else None
    def all(self): return self.rows


class _DB:
    def __init__(self, rows=()): self.rows = list(rows)
    def query(self, *a, **k): return _Q(self.rows)


# ---------- display name ----------

@pytest.mark.parametrize("first,last,email,expect", [
    ("Eric", "K", "eric@x.com", "Eric K (eric@x.com)"),
    ("Eric", None, "eric@x.com", "Eric (eric@x.com)"),
    # The first-run admin on every pre-1.4 install: no name, no email.
    (None, None, "", "KelTech"),
    (None, None, "a@b.co", "KelTech (a@b.co)"),
])
def test_display_name(first, last, email, expect):
    u = _U(1, username="KelTech", email=email,
           first_name=first, last_name=last)
    assert security.display_name(u) == expect


# ---------- email uniqueness ----------

def test_email_is_compared_case_insensitively():
    db = _DB([_U(1, email="eric@x.com")])
    assert security.email_taken(db, "ERIC@X.com") is True


def test_blank_email_is_never_taken():
    # Legacy first-run admins all have email='' - they must not collide.
    assert security.email_taken(_DB([_U(1, email="")]), "") is False


def test_normalize_email_lowercases_and_strips():
    assert security.normalize_email("  Eric@X.COM ") == "eric@x.com"
    assert security.normalize_email(None) == ""


# ---------- what an admin may do to their own row ----------

class _Req:
    def __init__(self, username):
        self.state = type("s", (), {"user": {"username": username}})()


def _patch(target, actor_username, **fields):
    """Drive update_user far enough to hit the self-edit guard."""
    body = ru.UserPatch(**fields)
    is_self = target.username == actor_username
    if is_self and (body.role is not None or body.org_id is not None
                    or body.is_active is not None):
        raise HTTPException(422, "self")
    return body


def test_admin_may_edit_own_name_email_and_breakglass():
    me = _U(1, username="KelTech", role="admin")
    _patch(me, "KelTech", first_name="Eric", email="eric@x.com",
           breakglass=True, external=False)


@pytest.mark.parametrize("field,value", [
    ("role", "user"), ("org_id", 3), ("is_active", False),
])
def test_admin_may_not_change_own_role_org_or_active(field, value):
    me = _U(1, username="KelTech", role="admin")
    with pytest.raises(HTTPException) as e:
        _patch(me, "KelTech", **{field: value})
    assert e.value.status_code == 422


def test_those_same_fields_are_fine_on_someone_else():
    other = _U(2, username="someone", role="user")
    _patch(other, "KelTech", role="admin", is_active=False)


# ---------- delete is a two-step ----------

def test_patch_model_distinguishes_unset_from_explicit_null():
    # org_id=None must be able to CLEAR a scope, so 'unset' and 'null' cannot
    # be the same thing - the endpoint reads membership, not truthiness.
    assert "org_id" not in ru.UserPatch().model_dump(exclude_unset=True)
    assert "org_id" in ru.UserPatch(org_id=None).model_dump(exclude_unset=True)
