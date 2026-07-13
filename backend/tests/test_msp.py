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
