"""XXE, SSRF and open-redirect hardening for the SSO code paths.

CodeQL flagged four of these on 8/8, all in code shipped that day (#15-#18).
Three were Critical. The XXE one is the serious one: validate_response parses
the POSTed SAMLResponse to read its StatusCode BEFORE the signature is
checked, so that parse is fully attacker-controlled by anyone who can reach
the ACS endpoint - which is unauthenticated by design.
"""
import pytest
from lxml import etree

from app.core import oidc, saml

# Classic XXE: pull a local file into the document via an external entity.
XXE = b"""<?xml version="1.0"?>
<!DOCTYPE r [ <!ENTITY xx SYSTEM "file:///etc/passwd"> ]>
<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol">
  <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
  <thing>&xx;</thing>
</samlp:Response>"""

# Billion laughs: entity expansion until the process dies.
BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<r>&lol4;</r>"""

# External entity pointed at a network host (SSRF via the XML parser).
SSRF_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE r [ <!ENTITY xx SYSTEM "http://169.254.169.254/latest/meta-data/"> ]>
<r>&xx;</r>"""


@pytest.mark.parametrize("payload", [XXE, BOMB, SSRF_ENTITY])
def test_doctype_payloads_are_refused(payload):
    """Every one of these carries a DOCTYPE, which has no legitimate place in
    SAML. Refused before any entity work happens."""
    with pytest.raises((ValueError, etree.XMLSyntaxError)):
        saml._parse_xml(payload)


def test_a_clean_document_still_parses():
    doc = saml._parse_xml(b'<r xmlns="urn:x"><a>1</a></r>')
    assert doc.tag == "{urn:x}r"


def test_the_parser_never_resolves_entities():
    """Behavioural, not introspective: lxml does not expose the constructor flags
    as readable attributes, so prove it by feeding the parser directly (bypassing
    _parse_xml's DOCTYPE gate) and showing the entity is NOT expanded."""
    doc = etree.fromstring(XXE, parser=saml._XML_PARSER)
    text = etree.tostring(doc, encoding="unicode")
    assert "root:" not in text          # /etc/passwd never got inlined
    assert "/etc/passwd" not in (doc.text or "")


def test_no_bare_fromstring_survives_in_the_module():
    """REGRESSION GUARD: every parse must go through _parse_xml. A single bare
    etree.fromstring() reintroduces the whole class of bug."""
    import inspect
    src = inspect.getsource(saml)
    # Comments mention etree.fromstring() by name; count real code only.
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    # The only permitted occurrence is the one inside _parse_xml itself.
    assert code.count("etree.fromstring(") == 1
    assert "etree.fromstring(data, parser=_XML_PARSER)" in code


# ---------- metadata fetch (SSRF surface) ----------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "gopher://x/", "ftp://x/", "data:text/xml,<r/>", "",
])
def test_metadata_url_scheme_is_allowlisted(url):
    with pytest.raises(ValueError):
        saml.fetch_metadata(url)


# ---------- the authorize redirect (open redirect) ----------

def test_authorize_endpoint_must_share_the_issuer_host():
    """The discovery document is REMOTE data and we 302 the browser to what it
    says. A hostile issuer must not be able to bounce people off-site."""
    with pytest.raises(ValueError):
        oidc._safe_authorize("https://idp.example.com",
                             "https://evil.example.net/authorize")


def test_matching_host_is_accepted():
    u = oidc._safe_authorize("https://idp.example.com/application/o/x/",
                             "https://idp.example.com/authorize")
    assert u == "https://idp.example.com/authorize"


@pytest.mark.parametrize("endpoint", [
    "", "not-a-url", "javascript:alert(1)", "//evil.example.net/authorize",
])
def test_junk_authorize_endpoints_are_refused(endpoint):
    with pytest.raises(ValueError):
        oidc._safe_authorize("https://idp.example.com", endpoint)


def test_an_https_issuer_will_not_be_downgraded_to_http():
    with pytest.raises(ValueError):
        oidc._safe_authorize("https://idp.example.com",
                             "http://idp.example.com/authorize")


def test_an_http_lab_issuer_still_works():
    u = oidc._safe_authorize("http://localhost:9000",
                             "http://localhost:9000/authorize")
    assert u.startswith("http://localhost:9000")
