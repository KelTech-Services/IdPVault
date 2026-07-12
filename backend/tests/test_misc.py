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
