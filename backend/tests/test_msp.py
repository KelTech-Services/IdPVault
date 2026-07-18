"""MSP v0.9: org role constants, scoping helpers, org input validation,
and msp feature-flag licensing. DB-free like the rest of the suite -
visible_tenant_ids is exercised with a stub query layer."""
import base64
import json
import time

import pytest
from fastapi import HTTPException

from app.core import security as sec


class _Req:
    """Minimal stand-in for a Request carrying request.state.user."""
    class _State:
        pass

    def __init__(self, user):
        self.state = self._State()
        self.state.user = user


class _StubDB:
    """Stub for the one query shape visible_tenant_ids uses."""
    def __init__(self, org_tenants):
        self._org_tenants = org_tenants  # {org_id: [tenant_id, ...]}

    def query(self, model):
        return self

    def filter(self, cond):
        # SQLAlchemy binary expression: right side holds the bound org_id
        self._org = cond.right.value
        return self

    def all(self):
        class T:
            def __init__(self, i):
                self.id = i
        return [T(i) for i in self._org_tenants.get(self._org, [])]


ADMIN = {"role": "admin", "org_id": None}
USER = {"role": "user", "org_id": None}
ORG_ADMIN = {"role": "org_admin", "org_id": 1}
ORG_VIEWER = {"role": "org_viewer", "org_id": 1}
ORPHAN = {"role": "org_admin", "org_id": None}
DB = _StubDB({1: [10, 11], 2: [20]})


def test_global_roles_unrestricted():
    assert sec.visible_tenant_ids(DB, ADMIN) is None
    assert sec.visible_tenant_ids(DB, USER) is None
    assert sec.visible_tenant_ids(DB, None) is None


def test_org_user_sees_only_own_org():
    assert sec.visible_tenant_ids(DB, ORG_ADMIN) == {10, 11}
    assert sec.visible_tenant_ids(DB, ORG_VIEWER) == {10, 11}
    assert sec.visible_tenant_ids(DB, ORPHAN) == set()


def test_read_scoping_404_not_403():
    sec.require_tenant_read(_Req(ORG_VIEWER), DB, 10)  # in org: passes
    with pytest.raises(HTTPException) as e:
        sec.require_tenant_read(_Req(ORG_VIEWER), DB, 20)  # other org
    assert e.value.status_code == 404


def test_write_matrix():
    sec.require_tenant_write(_Req(ADMIN), DB, 20)       # admin: anywhere
    sec.require_tenant_write(_Req(ORG_ADMIN), DB, 10)   # own org: ok
    with pytest.raises(HTTPException) as e:
        sec.require_tenant_write(_Req(ORG_ADMIN), DB, 20)
    assert e.value.status_code == 404                    # cross-org hidden
    with pytest.raises(HTTPException) as e:
        sec.require_tenant_write(_Req(ORG_VIEWER), DB, 10)
    assert e.value.status_code == 403                    # viewer: never writes
    assert "MSP administrator" in e.value.detail
    with pytest.raises(HTTPException) as e:
        sec.require_tenant_write(_Req(USER), DB, 10)
    assert e.value.status_code == 403


def test_org_input_validation():
    from app.api.routes_orgs import OrgIn, _validate
    _validate(OrgIn(name="Acme", billing_cadence="monthly",
                    renewal_date="2026-09-01"))
    b = OrgIn(name="Acme", renewal_date="09/01/2026")
    _validate(b)
    assert b.renewal_date == "2026-09-01"  # US input normalized to ISO storage
    with pytest.raises(HTTPException):
        _validate(OrgIn(name="Acme", billing_cadence="weekly"))
    with pytest.raises(HTTPException):
        _validate(OrgIn(name="Acme", renewal_date="Sept 1 2026"))
    with pytest.raises(HTTPException):
        _validate(OrgIn(name="   "))


def test_msp_feature_flag_in_license(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from app.core import license as lic

    priv = Ed25519PrivateKey.generate()
    pub = base64.b64encode(priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub)

    b64u = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    payload = {"customer": "MSP Co", "tier": "msp", "max_tenants": 4,
               "max_users": None, "features": ["identity", "msp"],
               "issued": int(time.time()), "expires": int(time.time()) + 86400}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    data = lic.verify(f"{b64u(body)}.{b64u(priv.sign(body))}")
    assert data and "msp" in data["features"] and "identity" in data["features"]


def test_orgs_csv_parse_roundtrip():
    from app.api.routes_orgs import CSV_COLUMNS, parse_orgs_csv
    text = (",".join(CSV_COLUMNS) + "\n"
            "Acme Corp,Jane Doe,jane@acme.com,+1 555,memo,monthly,2027-01-15,note\n"
            "\n"  # blank line ignored
            "Bare Minimum,,,,,,,\n")
    rows, errors = parse_orgs_csv(text)
    assert not errors
    assert [r.name for r in rows] == ["Acme Corp", "Bare Minimum"]
    assert rows[0].billing_cadence == "monthly" and rows[0].renewal_date == "2027-01-15"


def test_orgs_csv_parse_errors():
    from app.api.routes_orgs import CSV_COLUMNS, parse_orgs_csv
    text = (",".join(CSV_COLUMNS) + "\n"
            "Good Co,,,,,annual,2027-02-01,\n"
            ",,,,,,,\n"  # counted blank? no - all empty = skipped
            "Bad Cadence,,,,,weekly,,\n"
            "Bad Date,,,,,monthly,tomorrow,\n")
    rows, errors = parse_orgs_csv(text)
    assert [r.name for r in rows] == ["Good Co"]
    assert len(errors) == 2
    assert "row 3" in errors[0] and "cadence" in errors[0]
    assert "row 4" in errors[1] and "renewal_date" in errors[1]


def test_orgs_csv_missing_header():
    from app.api.routes_orgs import parse_orgs_csv
    rows, errors = parse_orgs_csv("Acme,Jane\nFoo,Bar\n")
    assert rows == [] and errors and "header" in errors[0]


# --- v1.1.5: snapshot browse endpoints must enforce tenant scoping ---

def test_snapshot_browse_enforces_tenant_read(monkeypatch):
    """browse/object_detail must call require_tenant_read BEFORE touching data
    (regression: they shipped without scoping, letting org-scoped users read
    any tenant's snapshot contents by id)."""
    from app.api import routes_audit as ra

    calls = []

    def fake_require(request, db, tenant_id):
        calls.append(tenant_id)
        raise HTTPException(404, "tenant not found")

    class _DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a):
            raise AssertionError("data access before scoping check")

    monkeypatch.setattr(ra, "require_tenant_read", fake_require)
    monkeypatch.setattr(ra, "SessionLocal", lambda: _DB())
    req = _Req({"id": 9, "role": "org_viewer", "org_id": 1})

    with pytest.raises(HTTPException) as e:
        ra.browse(req, 999, "20260101T000000Z")
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        ra.object_detail(req, 999, "20260101T000000Z", "applications", "x")
    assert e.value.status_code == 404
    assert calls == [999, 999]
