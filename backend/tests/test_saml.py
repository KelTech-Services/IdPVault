"""SAML 2.0: signature verification, binding, and the rejection paths.

These tests stand up a REAL self-signed IdP and sign REAL assertions with
signxml, so signature verification is exercised for what it is rather than
mocked away. A test that stubs the verifier proves nothing about SAML.
"""
import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner

from app.core import saml

BASE = "https://keltech-dev.idpvault.com"
ACS = BASE + "/api/v1/auth/saml/acs"
SP = BASE + "/api/v1/auth/saml/metadata"
IDP_ENTITY = "https://authentik.keltech.ai/idp"


@pytest.fixture(scope="module")
def idp():
    """A throwaway IdP: RSA key + self-signed cert, PEM for the SP config."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-idp")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return {"key": key, "cert": cert, "pem": pem,
            "key_pem": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()).decode()}


def _response_xml(request_id, email="eric@keltech.services", *,
                  not_after_minutes=5, audience=SP, recipient=ACS,
                  in_response_to=None):
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    na = (now + timedelta(minutes=not_after_minutes)).strftime(fmt)
    irt = request_id if in_response_to is None else in_response_to
    return f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
 xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_resp1" Version="2.0"
 IssueInstant="{now.strftime(fmt)}">
 <samlp:Status><samlp:StatusCode
   Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
 <saml:Assertion ID="_a1" Version="2.0" IssueInstant="{now.strftime(fmt)}">
  <saml:Issuer>{IDP_ENTITY}</saml:Issuer>
  <saml:Subject>
   <saml:NameID
     Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{email}</saml:NameID>
   <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
    <saml:SubjectConfirmationData InResponseTo="{irt}" Recipient="{recipient}"
      NotOnOrAfter="{na}"/>
   </saml:SubjectConfirmation>
  </saml:Subject>
  <saml:Conditions NotBefore="{(now - timedelta(minutes=1)).strftime(fmt)}"
    NotOnOrAfter="{na}">
   <saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience>
   </saml:AudienceRestriction>
  </saml:Conditions>
  <saml:AttributeStatement>
   <saml:Attribute Name="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress">
    <saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute>
   <saml:Attribute Name="givenname">
    <saml:AttributeValue>Eric</saml:AttributeValue></saml:Attribute>
   <saml:Attribute Name="sn">
    <saml:AttributeValue>Kelley</saml:AttributeValue></saml:Attribute>
  </saml:AttributeStatement>
 </saml:Assertion>
</samlp:Response>"""


def _sign(idp, xml, reference_uri="#_a1"):
    root = etree.fromstring(xml.encode())
    target = root if reference_uri == "" else root.find(
        ".//{urn:oasis:names:tc:SAML:2.0:assertion}Assertion")
    # signxml 4.x defaults are the SAML-correct ones; passing method= as a
    # string is rejected (it wants the enum), so take the defaults.
    signed = XMLSigner().sign(target, key=idp["key_pem"].encode(),
                              cert=idp["pem"].encode())
    if target is root:
        out = signed
    else:
        parent = root.find(".//{urn:oasis:names:tc:SAML:2.0:assertion}Assertion")
        root.replace(parent, signed)
        out = root
    return base64.b64encode(etree.tostring(out)).decode()


def _cfg(idp):
    return {"saml_entity_id": IDP_ENTITY, "saml_sso_url": IDP_ENTITY + "/sso",
            "saml_cert_pem": idp["pem"]}


def test_valid_assertion_yields_claims(idp):
    b64 = _sign(idp, _response_xml("_req1"))
    claims = saml.validate_response(_cfg(idp), b64, "_req1", BASE)
    assert claims["email"] == "eric@keltech.services"
    assert claims["given_name"] == "Eric"
    assert claims["family_name"] == "Kelley"


def test_unsigned_response_is_rejected(idp):
    b64 = base64.b64encode(_response_xml("_req1").encode()).decode()
    with pytest.raises(ValueError):
        saml.validate_response(_cfg(idp), b64, "_req1", BASE)


def test_tampered_assertion_is_rejected(idp):
    """Flip the email AFTER signing - the signature must catch it. This is the
    whole point of the feature."""
    b64 = _sign(idp, _response_xml("_req1"))
    xml = base64.b64decode(b64).replace(b"eric@keltech.services",
                                        b"attacker@evil.example")
    with pytest.raises(ValueError):
        saml.validate_response(_cfg(idp), base64.b64encode(xml).decode(),
                               "_req1", BASE)


def test_wrong_signing_cert_is_rejected(idp):
    """An assertion signed by a DIFFERENT IdP must not be accepted."""
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "evil")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(other.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365))
            .sign(other, hashes.SHA256()))
    cfg = _cfg(idp)
    cfg["saml_cert_pem"] = cert.public_bytes(serialization.Encoding.PEM).decode()
    b64 = _sign(idp, _response_xml("_req1"))
    with pytest.raises(ValueError):
        saml.validate_response(cfg, b64, "_req1", BASE)


def test_replay_against_a_different_request_is_rejected(idp):
    """SP-initiated only: an assertion answering someone else's AuthnRequest
    (or an IdP-initiated one) must not be accepted."""
    b64 = _sign(idp, _response_xml("_req1"))
    with pytest.raises(ValueError):
        saml.validate_response(_cfg(idp), b64, "_a_different_request", BASE)


def test_expired_assertion_is_rejected(idp):
    b64 = _sign(idp, _response_xml("_req1", not_after_minutes=-30))
    with pytest.raises(ValueError):
        saml.validate_response(_cfg(idp), b64, "_req1", BASE)


def test_audience_and_recipient_must_be_us(idp):
    for kw in ({"audience": "https://someone-else.example/sp"},
               {"recipient": "https://someone-else.example/acs"}):
        b64 = _sign(idp, _response_xml("_req1", **kw))
        with pytest.raises(ValueError):
            saml.validate_response(_cfg(idp), b64, "_req1", BASE)


def test_sp_urls_carry_the_api_v1_prefix():
    """REGRESSION GUARD: StackMerger mounts its API at /api, IdPVault at
    /api/v1. These two strings are the audience the IdP signs and the ACS it
    posts to - a wrong prefix fails as an opaque 'audience mismatch'."""
    assert saml.acs_url(BASE) == BASE + "/api/v1/auth/saml/acs"
    assert saml.sp_entity_id(BASE) == BASE + "/api/v1/auth/saml/metadata"
    assert "/api/v1/auth/saml/acs" in saml.sp_metadata_xml(BASE)
