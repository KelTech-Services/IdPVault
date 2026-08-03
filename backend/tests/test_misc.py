"""Password hashing, event naming, alert formatting."""
from app.core.security import hash_password, verify_password
from app.core.events import _name
from app.core.alerts import _drift_lines


def test_password_hash_verify():
    h = hash_password("hunter22!")
    assert h != "hunter22!"
    assert verify_password("hunter22!", h)
    assert not verify_password("wrong", h)
    assert hash_password("hunter22!") != h          # salted


def test_event_names_prefer_label_and_describe_bindings():
    assert _name({"name": "godaddy", "label": "GoDaddy"}) == "GoDaddy"
    assert _name({"name": "Default Policy"}) == "Default Policy"
    assert _name({"profile": {"name": "Slack Users"}}) == "Slack Users"
    assert _name({"group_obj": {"name": "app-it-tools-user"}}) == "binding: app-it-tools-user"


def test_drift_lines_markdown_safe():
    drift = {"apps": {"added": [{"label": "Zoom"}],
                      "removed": [{"label": "Old App"}],
                      "changed": [{"id": "x", "before": {"label": "A"},
                                   "after": {"label": "A"}}]}}
    lines = _drift_lines(drift)
    assert lines[0].startswith("[+] ") and lines[1].startswith("[-] ")
    assert lines[2].startswith("[~] ")
    assert not any(line.startswith(("+", "-")) for line in lines)  # markdown-bullet safe


def test_normalize_sorts_scalar_reference_lists():
    """Provider APIs return m2m id lists in nondeterministic order (verified on
    Authentik's embedded outpost `providers`); pure reordering is not drift."""
    from app.core.diff import normalize
    import json
    a = normalize({"name": "outpost", "providers": [20, 28, 22, 26]})
    b = normalize({"name": "outpost", "providers": [26, 22, 20, 28]})
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    # dict lists (ordered semantics, e.g. bindings payloads) are left alone
    c = normalize({"items": [{"a": 1}, {"b": 2}]})
    assert c["items"] == [{"a": 1}, {"b": 2}]


def test_split_unavailable_strips_every_metadata_key():
    """A refused resource type rides on the export under a _-prefixed key.
    Callers that diff, count or restore must never see it - if it leaked
    through it would read as a phantom resource type (drift, alert, restore
    target). split_unavailable is the single containment point."""
    from app.providers.base import split_unavailable, UNAVAILABLE_KEY
    export = {"apps": [{"id": "a"}], "groups": [],
              UNAVAILABLE_KEY: {"authz_servers": "HTTP 401 - E0000015 - not licensed"},
              "_future_meta": "ignored"}
    clean, unavail = split_unavailable(export)
    assert clean == {"apps": [{"id": "a"}], "groups": []}
    assert unavail == {"authz_servers": "HTTP 401 - E0000015 - not licensed"}
    # No marker at all is the common path and must not invent one.
    clean2, unavail2 = split_unavailable({"apps": []})
    assert clean2 == {"apps": []} and unavail2 == {}


def test_config_backup_job_result_carries_unavailable():
    """The Backup-now toast warns off the job result, so _trim has to keep the
    manifest's refused-type map. Losing it silently reports a partial backup as
    a complete one."""
    from app.core.jobs import _trim
    res = _trim("config_backup", {"manifest": {
        "timestamp": "20260729T120000Z", "counts": {"apps": 3},
        "unavailable": {"authz_servers": "HTTP 401 - E0000015"}}, "drift": False})
    assert res["unavailable"] == {"authz_servers": "HTTP 401 - E0000015"}
    # Clean backup: an empty map, never None (the frontend does Object.keys).
    assert _trim("config_backup", {"manifest": {"counts": {}}})["unavailable"] == {}


def test_describe_unavailable_uses_provider_error_code():
    from app.providers.base import describe_unavailable

    class R:
        status_code = 401
        def json(self):
            return {"errorCode": "E0000015",
                    "errorSummary": "You do not have permission."}

    msg = describe_unavailable(R())
    assert "HTTP 401" in msg and "E0000015" in msg
    assert "Access Management is not licensed" in msg   # E0000015 gets the real cause


# --- corporate identity: email is the identifier, legacy logins survive ---

class _IdU:
    """Minimal user row stand-in for the display/identity helpers."""
    def __init__(self, username="", email="", first=None, last=None):
        self.username, self.email = username, email
        self.first_name, self.last_name = first, last


def test_display_name_falls_back_for_legacy_accounts():
    """REGRESSION GUARD: every install created before v1.4 has a first-run
    admin with email="" and no names (app/main.py bootstrap, first-run setup).
    That account must still render as SOMETHING - never '(None)' or ' ()'."""
    from app.core.security import display_name
    assert display_name(_IdU(username="KelTech")) == "KelTech"
    assert display_name(_IdU("keltech", "eric@keltech.services", "Eric", "K")) \
        == "Eric K (eric@keltech.services)"
    # First name only, and email-but-no-name, both stay readable.
    assert display_name(_IdU("kt", "a@b.com", "Eric")) == "Eric (a@b.com)"
    assert display_name(_IdU("kt", "a@b.com")) == "kt (a@b.com)"


def test_normalize_email_treats_blank_as_absent():
    from app.core.security import normalize_email
    assert normalize_email("  Eric@KelTech.Services ") == "eric@keltech.services"
    assert normalize_email("") == ""
    assert normalize_email(None) == ""


def test_username_from_email_respects_the_80_char_column(monkeypatch):
    """users.username is varchar(80) and unique. StackMerger's helper assumed
    60 - porting it unchanged would silently truncate differently here."""
    from app.core import security
    long_email = ("x" * 90) + "@example.com"

    class _Q:
        def __init__(self, taken): self.taken = taken
        def filter(self, *a): return self
        def first(self): return None

    class _DB:
        def query(self, _m): return _Q(set())

    name = security.username_from_email(_DB(), long_email)
    assert len(name) <= 80
    assert name == long_email.lower()[:80]


# --- OIDC: claim handling and the guards that protect MSP scoping ---

def test_authentik_sends_the_whole_name_as_given_name():
    """REGRESSION GUARD (live bug in StackMerger): Authentik stores one
    full-name field and its default mapping puts it in given_name with NO
    family_name. Without the split, 'Eric Kelley' becomes the first name."""
    from app.core.oidc import split_name
    assert split_name({"given_name": "Eric Kelley"}) == ("Eric", "Kelley")
    # Properly split claims are left alone.
    assert split_name({"given_name": "Eric", "family_name": "Kelley"}) \
        == ("Eric", "Kelley")
    # Three-part names keep everything after the first token as the surname.
    assert split_name({"given_name": "Ana Maria Silva"}) == ("Ana", "Maria Silva")
    # Fallback to the plain 'name' claim.
    assert split_name({"name": "Eric Kelley"}) == ("Eric", "Kelley")
    assert split_name({"name": "Cher"}) == ("Cher", None)
    assert split_name({}) == (None, None)


def test_jit_role_can_never_be_an_org_scoped_msp_role(monkeypatch):
    """org_admin/org_viewer are meaningless without an org_id, and an IdP has
    no way to supply one. Anything outside the global roles falls back to
    'user' rather than creating a broken account."""
    from app.core import oidc
    for bad in ("org_admin", "org_viewer", "root", "", None):
        monkeypatch.setattr(oidc, "get_sso", lambda b=bad: {"jit_default_role": b})
        assert oidc.jit_role() == "user"
    for good in ("user", "admin"):
        monkeypatch.setattr(oidc, "get_sso", lambda g=good: {"jit_default_role": g})
        assert oidc.jit_role() == good


def test_sso_mode_is_off_without_the_identity_feature(monkeypatch):
    """SSO is Business + MSP only. Just as important: if a license lapses,
    mode() collapses to 'off' so a required-SSO install cannot lock everyone
    out of the app it just stopped paying for."""
    from app.core import oidc
    cfg = {"mode": "required", "issuer": "https://idp.example.com",
           "client_id": "abc", "client_secret_enc": "00"}
    monkeypatch.setattr(oidc, "get_sso", lambda: cfg)
    monkeypatch.setattr(oidc, "licensed", lambda: True)
    assert oidc.mode() == "required"
    monkeypatch.setattr(oidc, "licensed", lambda: False)
    assert oidc.mode() == "off"
    assert oidc.public_info()["enabled"] is False


def test_txn_cookie_roundtrip_and_expiry(monkeypatch):
    """The login transaction is stateless - it rides in an encrypted cookie.
    An expired one must be refused, not silently accepted."""
    from app.core import oidc
    raw = oidc.make_txn_cookie("st", "no", "ver")
    d = oidc.read_txn_cookie(raw)
    assert (d["s"], d["n"], d["v"]) == ("st", "no", "ver")
    monkeypatch.setattr(oidc.time, "time",
                        lambda: d["t"] + oidc.TXN_MAX_AGE + 1)
    try:
        oidc.read_txn_cookie(raw)
    except ValueError as e:
        assert "expired" in str(e)
    else:
        raise AssertionError("an expired transaction cookie must be refused")
