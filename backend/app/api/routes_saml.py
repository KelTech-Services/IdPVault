"""SAML 2.0 sign-in routes: SP metadata, AuthnRequest start, and the ACS.

Two things here are hard-won and must not be "tidied":

1. The transaction cookie is SameSite=None; Secure. The IdP returns the
   assertion as a CROSS-SITE POST, and SameSite=lax drops the cookie on that
   request - which loses InResponseTo and makes every sign-in fail with a
   mismatch that looks like a config error.

2. The ACS parses the form body by hand with parse_qs. FastAPI's Form()
   support needs python-multipart; adding that dependency just to read one
   field is not worth it.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.core import deploy, oidc, saml, security
from app.models.db import AuditLog, SessionLocal

router = APIRouter(tags=["sso"])
COOKIE = "idpvault_session"


def _base(request: Request) -> str:
    return deploy.public_base(request).rstrip("/")


def _fail(msg: str):
    from urllib.parse import quote
    return RedirectResponse(f"/#sso_error={quote(msg[:200])}", status_code=302)


@router.get("/auth/saml/metadata")
def sp_metadata(request: Request) -> Response:
    """Our SP metadata - import this at the IdP. Public by design: it holds
    only our entity id and ACS URL, both of which the IdP needs anyway."""
    return Response(content=saml.sp_metadata_xml(_base(request)),
                    media_type="application/xml")


@router.get("/auth/saml/login")
def saml_login(request: Request):
    if oidc.mode() == "off" or oidc.protocol() != "saml":
        raise HTTPException(404, "SAML single sign-on is not enabled")
    cfg = oidc.get_sso()
    try:
        url, txn = saml.auth_request(cfg, _base(request))
    except Exception as e:
        raise HTTPException(502, f"could not build the sign-in request: {e}")
    r = RedirectResponse(url, status_code=302)
    # See the module docstring: None+Secure, NOT lax.
    r.set_cookie(saml.TXN_COOKIE, txn, httponly=True, samesite="none",
                 secure=True, max_age=saml.TXN_MAX_AGE)
    return r


@router.post("/auth/saml/acs")
async def saml_acs(request: Request):
    """Assertion Consumer Service - the IdP POSTs the signed assertion here."""
    if oidc.mode() == "off" or oidc.protocol() != "saml":
        raise HTTPException(404, "SAML single sign-on is not enabled")
    from urllib.parse import parse_qs
    body = (await request.body()).decode("utf-8", "replace")
    form = parse_qs(body)
    resp_b64 = (form.get("SAMLResponse") or [""])[0]
    if not resp_b64:
        return _fail("the identity provider posted no SAML response")
    raw = request.cookies.get(saml.TXN_COOKIE, "")
    if not raw:
        return _fail("this sign-in attempt could not be matched - start again "
                     "from the sign-in page")
    try:
        request_id = saml.read_txn_cookie(raw)
    except Exception:
        return _fail("this sign-in attempt expired - try again")
    cfg = oidc.get_sso()
    try:
        claims = saml.validate_response(cfg, resp_b64, request_id, _base(request))
    except ValueError as e:
        return _fail(str(e))
    except Exception:
        return _fail("could not validate the response from the identity provider")

    with SessionLocal() as db:
        try:
            # Identity resolution is protocol-independent: SAML and OIDC join
            # on email and share JIT, name refresh and the disabled check.
            u, created = oidc.resolve_user(db, claims)
        except ValueError as e:
            return _fail(str(e))
        token = security.create_session(db, u.id)
        db.add(AuditLog(actor=u.username,
                        action="auth.sso_signup" if created else "auth.sso_login",
                        detail={"email": u.email, "protocol": "saml"}))
        db.commit()
    r = RedirectResponse("/", status_code=302)
    r.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                 secure=deploy.is_secure(request),
                 max_age=security.SESSION_DAYS * 86400)
    r.delete_cookie(saml.TXN_COOKIE)
    return r
