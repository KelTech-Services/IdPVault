"""REGRESSION GUARD: the sign-in button dispatches on the configured protocol.

The button always points at /auth/sso/login. That endpoint - not the frontend -
decides whether to start an OIDC authorize redirect or a SAML AuthnRequest.

This shipped broken once: the SAML routes were complete but sso_login always
took the OIDC path, so switching the protocol to SAML sent people to the IdP's
OIDC endpoint with no client_id and they got the provider's own error page.
That failure is invisible to every unit test that exercises saml.py directly,
which is why this one exercises the dispatch instead.
"""
import pytest

from app.api import routes_sso
from app.core import oidc


class _Req:
    cookies: dict = {}
    headers: dict = {}


def test_saml_protocol_takes_the_saml_path(monkeypatch):
    monkeypatch.setattr(oidc, "mode", lambda *a, **k: "optional")
    monkeypatch.setattr(oidc, "protocol", lambda *a, **k: "saml")
    called = {}

    def _fake_saml_login(request):
        called["saml"] = True
        return "saml-redirect"

    import app.api.routes_saml as rs
    monkeypatch.setattr(rs, "saml_login", _fake_saml_login)
    # oidc.auth_request must never be reached on the SAML path.
    monkeypatch.setattr(oidc, "auth_request",
                        lambda *a: pytest.fail("took the OIDC path with protocol=saml"))

    assert routes_sso.sso_login(_Req()) == "saml-redirect"
    assert called.get("saml") is True


def test_oidc_protocol_takes_the_oidc_path(monkeypatch):
    monkeypatch.setattr(oidc, "mode", lambda *a, **k: "optional")
    monkeypatch.setattr(oidc, "protocol", lambda *a, **k: "oidc")
    monkeypatch.setattr(routes_sso, "_redirect_uri", lambda r: "https://x/cb")
    monkeypatch.setattr(oidc, "auth_request",
                        lambda uri: ("https://idp/authorize?client_id=abc", "txn"))
    monkeypatch.setattr(routes_sso.deploy, "is_secure", lambda r: True)

    import app.api.routes_saml as rs
    monkeypatch.setattr(rs, "saml_login",
                        lambda r: pytest.fail("took the SAML path with protocol=oidc"))

    r = routes_sso.sso_login(_Req())
    assert r.status_code == 302
    assert r.headers["location"] == "https://idp/authorize?client_id=abc"


def test_mode_off_refuses_both(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(oidc, "mode", lambda *a, **k: "off")
    with pytest.raises(HTTPException) as e:
        routes_sso.sso_login(_Req())
    assert e.value.status_code == 404
