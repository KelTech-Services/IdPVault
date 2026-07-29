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
