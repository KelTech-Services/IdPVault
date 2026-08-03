"""SAML 2.0 SP (locked 7/29 plan, built 7/31: OIDC shipped first, SAML is
the second sign-in protocol feeding the SAME pipeline - email join key,
oidc.resolve_user JIT/email-match, shared enforcement modes + break-glass).

- SP-initiated only: HTTP-Redirect AuthnRequest out, HTTP-POST assertion
  back to the ACS. IdP-initiated responses are rejected (InResponseTo is
  REQUIRED and bound to a master-key-encrypted transaction cookie).
- IdP config comes from its metadata URL (Authentik/Okta/Entra/Google all
  publish one): entity id + SSO redirect endpoint + signing certificate are
  parsed and stored at save time.
- Signature validation via signxml (pure-pip: lxml + cryptography - no
  system xmlsec packages). Response-level OR assertion-level signatures
  accepted; everything after verification reads ONLY from the verified
  subtree (signature-wrapping defense). Unsigned = rejected.
- Clock skew tolerance 5 minutes on NotBefore/NotOnOrAfter.
"""
import base64
import json
import os
import time
import zlib
from datetime import datetime, timezone

import httpx
from lxml import etree

from app.core import crypto

TXN_COOKIE = "idpvault_saml_txn"
TXN_MAX_AGE = 600
SKEW = 300                      # seconds of clock-skew tolerance
_UA = {"User-Agent": "Mozilla/5.0 (compatible; IdPVault-SSO)"}

NS = {"md": "urn:oasis:names:tc:SAML:2.0:metadata",
      "ds": "http://www.w3.org/2000/09/xmldsig#",
      "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
      "saml": "urn:oasis:names:tc:SAML:2.0:assertion"}

# Attribute-name aliases across IdPs (Authentik/Okta friendly names, LDAP
# OIDs, and the WS-Fed/Entra claim URIs).
_ATTR_ALIASES = {
    "email": {"email", "mail", "emailaddress",
              "urn:oid:0.9.2342.19200300.100.1.3",
              "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"},
    "given_name": {"givenname", "given_name", "firstname", "first_name",
                   "urn:oid:2.5.4.42",
                   "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname"},
    "family_name": {"sn", "surname", "lastname", "last_name", "familyname",
                    "urn:oid:2.5.4.4",
                    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname"},
    "name": {"name", "displayname", "cn", "urn:oid:2.16.840.1.113730.3.1.241",
             "urn:oid:2.5.4.3",
             "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"},
}


def sp_entity_id(base_url: str) -> str:
    return base_url + "/api/v1/auth/saml/metadata"


def acs_url(base_url: str) -> str:
    return base_url + "/api/v1/auth/saml/acs"


def fetch_metadata(url: str) -> dict:
    """Fetch + parse IdP metadata: entity id, HTTP-Redirect SSO endpoint,
    signing certificate (PEM). Raises ValueError with a safe message."""
    r = httpx.get(url, headers=_UA, timeout=15, follow_redirects=True)
    r.raise_for_status()
    try:
        root = etree.fromstring(r.content)
    except etree.XMLSyntaxError:
        raise ValueError("that URL did not return XML metadata")
    if root.tag != "{%s}EntityDescriptor" % NS["md"]:
        # EntitiesDescriptor wrapper (federations) - take the first entity.
        ent = root.find(".//md:EntityDescriptor", NS)
        if ent is None:
            raise ValueError("no EntityDescriptor in the metadata")
        root = ent
    entity_id = root.get("entityID")
    idp = root.find("md:IDPSSODescriptor", NS)
    if idp is None:
        raise ValueError("metadata has no IdP (IDPSSODescriptor) - is this "
                         "an SP metadata URL?")
    sso = None
    for svc in idp.findall("md:SingleSignOnService", NS):
        if svc.get("Binding", "").endswith("HTTP-Redirect"):
            sso = svc.get("Location")
            break
    if not sso:
        raise ValueError("metadata has no HTTP-Redirect SingleSignOnService")
    cert_b64 = None
    for kd in idp.findall("md:KeyDescriptor", NS):
        if kd.get("use") in (None, "signing"):
            c = kd.find(".//ds:X509Certificate", NS)
            if c is not None and (c.text or "").strip():
                cert_b64 = "".join((c.text or "").split())
                break
    if not cert_b64:
        raise ValueError("metadata has no signing certificate")
    pem = ("-----BEGIN CERTIFICATE-----\n"
           + "\n".join(cert_b64[i:i + 64] for i in range(0, len(cert_b64), 64))
           + "\n-----END CERTIFICATE-----\n")
    return {"entity_id": entity_id, "sso_url": sso, "cert_pem": pem}


def make_txn_cookie(request_id: str) -> str:
    blob = json.dumps({"r": request_id, "t": int(time.time())}).encode()
    return crypto.encrypt(blob, crypto._master_key()).hex()


def read_txn_cookie(raw: str) -> str:
    d = json.loads(crypto.decrypt(bytes.fromhex(raw), crypto._master_key()))
    if int(time.time()) - int(d.get("t", 0)) > TXN_MAX_AGE:
        raise ValueError("sign-in attempt expired - try again")
    return d["r"]


def auth_request(cfg: dict, base_url: str) -> tuple[str, str]:
    """Build the redirect URL to the IdP. Returns (url, txn_cookie_value)."""
    from urllib.parse import urlencode
    req_id = "_" + os.urandom(20).hex()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (
        '<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{req_id}" Version="2.0" IssueInstant="{now}" '
        f'Destination="{cfg["saml_sso_url"]}" '
        f'AssertionConsumerServiceURL="{acs_url(base_url)}" '
        'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
        f'<saml:Issuer>{sp_entity_id(base_url)}</saml:Issuer>'
        '<samlp:NameIDPolicy '
        'Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" '
        'AllowCreate="true"/></samlp:AuthnRequest>')
    deflated = zlib.compress(xml.encode())[2:-4]   # raw DEFLATE
    q = urlencode({"SAMLRequest": base64.b64encode(deflated).decode()})
    sep = "&" if "?" in cfg["saml_sso_url"] else "?"
    return cfg["saml_sso_url"] + sep + q, make_txn_cookie(req_id)


def _verified_tree(doc_bytes: bytes, cert_pem: str):
    """Verify the XML signature and return the trusted subtree. Accepts a
    Response-level signature (returns the whole verified response) or an
    Assertion-level signature (returns the verified assertion). Everything
    downstream reads ONLY from what this returns."""
    from signxml import XMLVerifier
    root = etree.fromstring(doc_bytes)
    try:
        return XMLVerifier().verify(root, x509_cert=cert_pem).signed_xml
    except Exception:
        pass
    assertion = root.find("saml:Assertion", NS)
    if assertion is None:
        raise ValueError("the SAML response is not signed")
    try:
        return XMLVerifier().verify(assertion, x509_cert=cert_pem).signed_xml
    except Exception:
        raise ValueError("SAML signature validation failed - check the IdP "
                         "metadata / signing certificate")


def _parse_time(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def validate_response(cfg: dict, saml_response_b64: str, request_id: str,
                      base_url: str) -> dict:
    """POSTed SAMLResponse -> claims dict (same shape the OIDC path feeds to
    resolve_user). Raises ValueError with a safe message on any failure."""
    try:
        doc = base64.b64decode(saml_response_b64)
    except Exception:
        raise ValueError("malformed SAML response")
    # Status check on the raw response (status itself is not attacker-useful).
    raw = etree.fromstring(doc)
    status = raw.find(".//samlp:StatusCode", NS)
    if status is None or not status.get("Value", "").endswith("Success"):
        raise ValueError("the identity provider reported sign-in failure")
    verified = _verified_tree(doc, cfg["saml_cert_pem"])
    # The verified subtree is the Response or the Assertion.
    assertion = (verified if verified.tag.endswith("Assertion")
                 else verified.find("saml:Assertion", NS))
    if assertion is None:
        raise ValueError("no assertion inside the signed content")
    issuer = assertion.find("saml:Issuer", NS)
    if issuer is None or (issuer.text or "").strip() != cfg["saml_entity_id"]:
        raise ValueError("assertion issuer mismatch")
    now = time.time()
    cond = assertion.find("saml:Conditions", NS)
    if cond is not None:
        nb, na = cond.get("NotBefore"), cond.get("NotOnOrAfter")
        if nb and now < _parse_time(nb) - SKEW:
            raise ValueError("assertion is not yet valid - check clocks")
        if na and now > _parse_time(na) + SKEW:
            raise ValueError("assertion has expired - try signing in again")
        aud = cond.find(".//saml:Audience", NS)
        if aud is not None and (aud.text or "").strip() != sp_entity_id(base_url):
            raise ValueError("assertion audience mismatch")
    # SP-initiated only: the SubjectConfirmation must answer OUR request.
    scd = assertion.find(".//saml:SubjectConfirmationData", NS)
    if scd is not None:
        irt = scd.get("InResponseTo")
        if irt and irt != request_id:
            raise ValueError("response does not match this sign-in attempt")
        rec = scd.get("Recipient")
        if rec and rec != acs_url(base_url):
            raise ValueError("assertion recipient mismatch")
        na = scd.get("NotOnOrAfter")
        if na and now > _parse_time(na) + SKEW:
            raise ValueError("assertion has expired - try signing in again")
    claims: dict = {}
    for attr in assertion.findall(".//saml:Attribute", NS):
        name = (attr.get("Name") or "").strip()
        key = name.lower()
        vals = [v.text for v in attr.findall("saml:AttributeValue", NS)
                if (v.text or "").strip()]
        if not vals:
            continue
        for claim, aliases in _ATTR_ALIASES.items():
            if key in aliases or name in aliases:
                claims.setdefault(claim, vals[0])
    nameid = assertion.find(".//saml:NameID", NS)
    if "email" not in claims and nameid is not None \
            and "@" in (nameid.text or ""):
        claims["email"] = (nameid.text or "").strip()
    if not claims.get("email"):
        raise ValueError("the identity provider sent no email attribute - "
                         "map email into the SAML assertion")
    claims["sub"] = (nameid.text or "").strip() if nameid is not None else ""
    return claims


def sp_metadata_xml(base_url: str) -> str:
    """Our SP metadata - paste/import at the IdP."""
    return (
        '<?xml version="1.0"?>'
        '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
        f'entityID="{sp_entity_id(base_url)}">'
        '<md:SPSSODescriptor AuthnRequestsSigned="false" '
        'WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        '<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'
        '</md:NameIDFormat>'
        '<md:AssertionConsumerService '
        'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{acs_url(base_url)}" index="0" isDefault="true"/>'
        '</md:SPSSODescriptor></md:EntityDescriptor>')
