"""OIDC SSO for IdPVault (ported from StackMerger v0.5.0, adapted).

- Config lives in Settings key "sso"; the client secret is encrypted at rest
  with the master key. Modes: off | optional | required.
- Authorization-code flow with PKCE + nonce. Per-transaction state (state,
  nonce, PKCE verifier, timestamp) rides in a master-key-encrypted short-lived
  cookie, so the server stays stateless.
- The ID token arrives DIRECTLY from the token endpoint over TLS with client
  authentication, so the TLS channel stands in for signature validation
  (OIDC Core 3.1.3.7); we validate iss / aud / exp / nonce and cross-check
  the userinfo endpoint's sub.
- The identity join key is EMAIL: the email claim matches an existing user row;
  no match = JIT-create with the configured default role. The IdP owns names -
  first/last refresh from claims at every sign-in. SSO users have no usable
  local password.

IdPVault-specific divergences from the StackMerger original:
- SSO is LICENSE-GATED on the "identity" feature (Business + MSP). The free
  Community tier carries no features at all, so it never sees SSO.
- JIT can only ever assign a GLOBAL role (admin | user). org_admin/org_viewer
  are MSP client roles: they are meaningless without an org_id, and an MSP's
  clients are not in the MSP's corporate directory. They stay hand-assigned.
- JIT users are created UNSCOPED (org_id stays NULL) so the MSP 404-out-of-org
  scoping is never widened by an IdP.
"""
import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import httpx

from app.core import crypto

MODES = ("off", "optional", "required")
TXN_COOKIE = "idpvault_sso_txn"
TXN_MAX_AGE = 600          # seconds a login transaction stays valid
# Cloudflare-fronted IdPs 403 default client UAs - always send a real one.
_UA = {"User-Agent": "Mozilla/5.0 (compatible; IdPVault-SSO)"}
# Roles the IdP is allowed to hand out. Deliberately excludes the org-scoped
# MSP roles - see the module docstring.
JIT_ROLES = ("user", "admin")

_disco_cache: dict = {}    # issuer -> (fetched_at, doc)


def licensed() -> bool:
    """SSO is a paid feature: Business and MSP keys carry "identity",
    Community carries no features. Reusing the existing key means no license
    re-minting and no license-server change."""
    from app.core import license as lic
    return lic.has_feature("identity")


def get_sso() -> dict:
    from app.models.db import SessionLocal, Setting
    with SessionLocal() as db:
        row = db.get(Setting, "sso")
        return dict(row.value) if row else {}


def save_sso(v: dict) -> None:
    from app.models.db import SessionLocal, Setting
    with SessionLocal() as db:
        row = db.get(Setting, "sso")
        if row is None:
            db.add(Setting(key="sso", value=v))
        else:
            row.value = v
        db.commit()


def protocol(v: dict | None = None) -> str:
    v = get_sso() if v is None else v
    return v.get("protocol") or "oidc"


def configured(v: dict | None = None) -> bool:
    v = get_sso() if v is None else v
    if protocol(v) == "saml":
        return bool(v.get("saml_sso_url") and v.get("saml_entity_id")
                    and v.get("saml_cert_pem"))
    return bool(v.get("issuer") and v.get("client_id")
                and v.get("client_secret_enc"))


def mode(v: dict | None = None) -> str:
    """The EFFECTIVE mode. An unlicensed or unconfigured install is "off",
    which also means a license lapsing can never lock anyone out of the app."""
    v = get_sso() if v is None else v
    m = v.get("mode") or "off"
    if not licensed() or not configured(v):
        return "off"
    return m if m in MODES else "off"


def public_info() -> dict:
    """What the login screen needs - never the config itself."""
    v = get_sso()
    m = mode(v)
    return {"enabled": m != "off", "mode": m, "protocol": protocol(v),
            "label": v.get("button_label") or "Sign in with SSO"}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def discovery(issuer: str, force: bool = False) -> dict:
    """Fetch (and cache ~1h) the issuer's .well-known document."""
    issuer = issuer.rstrip("/")
    hit = _disco_cache.get(issuer)
    if hit and not force and time.time() - hit[0] < 3600:
        return hit[1]
    url = issuer + "/.well-known/openid-configuration"
    r = httpx.get(url, headers=_UA, timeout=15, follow_redirects=True)
    r.raise_for_status()
    doc = r.json()
    for k in ("authorization_endpoint", "token_endpoint", "issuer"):
        if not doc.get(k):
            raise ValueError(f"discovery document is missing {k}")
    _disco_cache[issuer] = (time.time(), doc)
    return doc


def make_txn_cookie(state: str, nonce: str, verifier: str) -> str:
    blob = json.dumps({"s": state, "n": nonce, "v": verifier,
                       "t": int(time.time())}).encode()
    return crypto.encrypt(blob, crypto._master_key()).hex()


def read_txn_cookie(raw: str) -> dict:
    d = json.loads(crypto.decrypt(bytes.fromhex(raw), crypto._master_key()))
    if int(time.time()) - int(d.get("t", 0)) > TXN_MAX_AGE:
        raise ValueError("sign-in attempt expired - try again")
    return d


def auth_request(redirect_uri: str) -> tuple[str, str]:
    """Build the IdP authorize URL. Returns (url, txn_cookie_value)."""
    from urllib.parse import urlencode
    v = get_sso()
    doc = discovery(v["issuer"])
    state, nonce = _b64url(os.urandom(24)), _b64url(os.urandom(24))
    verifier = _b64url(os.urandom(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    q = {"response_type": "code", "client_id": v["client_id"],
         "redirect_uri": redirect_uri,
         "scope": v.get("scopes") or "openid profile email",
         "state": state, "nonce": nonce,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return (doc["authorization_endpoint"] + "?" + urlencode(q),
            make_txn_cookie(state, nonce, verifier))


def _jwt_payload(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def exchange(code: str, verifier: str, nonce: str, redirect_uri: str) -> dict:
    """Code -> validated claims. Raises ValueError with a safe message."""
    v = get_sso()
    doc = discovery(v["issuer"])
    secret = crypto.decrypt(bytes.fromhex(v["client_secret_enc"]),
                            crypto._master_key()).decode()
    r = httpx.post(doc["token_endpoint"], headers=_UA, timeout=20,
                   data={"grant_type": "authorization_code", "code": code,
                         "redirect_uri": redirect_uri,
                         "client_id": v["client_id"],
                         "client_secret": secret,
                         "code_verifier": verifier})
    if r.status_code != 200:
        raise ValueError(f"the identity provider rejected the sign-in "
                         f"(token endpoint returned {r.status_code})")
    tok = r.json()
    idt = tok.get("id_token")
    if not idt:
        raise ValueError("the identity provider returned no id_token")
    claims = _jwt_payload(idt)
    if claims.get("iss", "").rstrip("/") != doc["issuer"].rstrip("/"):
        raise ValueError("id_token issuer mismatch")
    aud = claims.get("aud")
    if v["client_id"] not in (aud if isinstance(aud, list) else [aud]):
        raise ValueError("id_token audience mismatch")
    if claims.get("exp", 0) < time.time():
        raise ValueError("id_token is expired")
    if claims.get("nonce") != nonce:
        raise ValueError("id_token nonce mismatch")
    # Cross-check + enrich from userinfo when available.
    if doc.get("userinfo_endpoint") and tok.get("access_token"):
        ui = httpx.get(doc["userinfo_endpoint"], timeout=15, headers={
            **_UA, "Authorization": "Bearer " + tok["access_token"]})
        if ui.status_code == 200:
            info = ui.json()
            if info.get("sub") and info["sub"] != claims.get("sub"):
                raise ValueError("userinfo subject mismatch")
            for k in ("email", "given_name", "family_name", "name"):
                claims.setdefault(k, info.get(k))
    if not claims.get("email"):
        raise ValueError("the identity provider sent no email claim - "
                         "add the email scope/claim to the app integration")
    return claims


def split_name(claims: dict) -> tuple[str | None, str | None]:
    """first/last out of whatever shape the IdP sent.

    Authentik stores ONE full-name field and its default mapping puts it in
    given_name with no family_name - so "Eric Kelley" arrives whole. Splitting
    it here is a real bug fix carried over from StackMerger, not a nicety.
    """
    first = (claims.get("given_name") or "").strip() or None
    last = (claims.get("family_name") or "").strip() or None
    if first and not last and " " in first:
        first, last = first.split(None, 1)
    if not first and not last and claims.get("name"):
        parts = str(claims["name"]).strip().split(None, 1)
        first, last = parts[0], (parts[1] if len(parts) > 1 else None)
    return first, last


def jit_role(v: dict | None = None) -> str:
    """The role a JIT-created user gets. Clamped to the GLOBAL roles: an IdP
    must never be able to mint an org-scoped MSP role, which would need an
    org_id it has no way to know."""
    v = get_sso() if v is None else v
    role = v.get("jit_default_role") or "user"
    return role if role in JIT_ROLES else "user"


def resolve_user(db, claims: dict):
    """Email-match an existing account, else JIT-create. The IdP owns names -
    refresh first/last from claims on every sign-in. Returns (User, created);
    raises ValueError for a disabled account."""
    from sqlalchemy import func

    from app.core import security as sec
    from app.models.db import User
    email = str(claims["email"]).strip().lower()
    first, last = split_name(claims)
    u = (db.query(User)
         .filter(func.lower(User.email) == email, User.email != "")
         .first())
    created = False
    if u is None:
        u = User(username=sec.username_from_email(db, email),
                 # Unusable placeholder - SSO users have no local password.
                 password_hash=sec.hash_password(_b64url(os.urandom(32))),
                 role=jit_role(),
                 # UNSCOPED on purpose: org_id stays NULL so an IdP can never
                 # place a user inside an MSP client org.
                 org_id=None,
                 is_active=True, email=email, sso_user=True,
                 created_at=datetime.now(timezone.utc))
        db.add(u)
        db.flush()
        created = True
    if not u.is_active:
        raise ValueError("this account is disabled - contact your administrator")
    if first:
        u.first_name = first
    if last:
        u.last_name = last
    return u, created
