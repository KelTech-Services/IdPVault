"""Alert delivery: rich webhook (Slack/Mattermost/Discord attachments or ntfy) +
admin email. Each alert has a category; only categories the admin subscribed to
are sent. Called from the backup/restore pipeline. Never raises.
"""
import logging
import time

import httpx

from app.models.db import SessionLocal, Setting, User

log = logging.getLogger(__name__)

# The alert catalog — keep in sync with the Settings UI.
ALERT_EVENTS = {
    "drift_detected":  {"label": "Configuration drift detected", "default": True,  "color": "#ffb454"},
    "backup_failed":   {"label": "Backup failed",                "default": True,  "color": "#ff6b6b"},
    "backup_success":  {"label": "Backup succeeded",             "default": False, "color": "#3ecf8e"},
    "restore_applied": {"label": "Restore applied",              "default": True,  "color": "#4d9fff"},
    "backup_stale":    {"label": "Backup overdue / stale",       "default": True,  "color": "#ff6b6b"},
}


def _cfg() -> dict:
    with SessionLocal() as db:
        row = db.get(Setting, "general")
        return dict(row.value) if row else {}


def _enabled(cfg: dict) -> list:
    ev = cfg.get("alert_events")
    if ev is None:
        return [k for k, v in ALERT_EVENTS.items() if v["default"]]
    return ev


def _admin_emails() -> list:
    with SessionLocal() as db:
        return [u.email for u in db.query(User).filter(
            User.role == "admin", User.is_active == True) if u.email]  # noqa: E712


def _webhook_format(url: str, fmt: str) -> str:
    if fmt and fmt != "auto":
        return fmt
    u = (url or "").lower()
    if any(k in u for k in ("slack.com", "discord.com", "mattermost", "/hooks/", "/services/")):
        return "slack"
    return "ntfy"


def _post_webhook(url, fmt, title, body, color="#4d9fff", fields=None):
    if fmt == "slack":
        att = {"color": color, "title": title, "text": body,
               "footer": "IdPVault", "ts": int(time.time()), "mrkdwn_in": ["text"]}
        if fields:
            att["fields"] = [{"title": k, "value": str(v), "short": True}
                             for k, v in fields.items()]
        return httpx.post(url, json={"attachments": [att]}, timeout=10)
    text = body
    if fields:
        text += "\n" + "\n".join(f"{k}: {v}" for k, v in fields.items())
    return httpx.post(url, content=text.encode(),
                      headers={"Title": title, "Tags": "warning"}, timeout=10)


def send_alert(category: str, title: str, body: str, fields=None) -> None:
    """Deliver an alert if the admin is subscribed to its category. Never raises."""
    try:
        cfg = _cfg()
        if category not in _enabled(cfg):
            return
        color = ALERT_EVENTS.get(category, {}).get("color", "#4d9fff")
        url = cfg.get("alert_webhook_url")
        if url:
            fmt = _webhook_format(url, cfg.get("alert_webhook_format", "auto"))
            _post_webhook(url, fmt, title, body, color, fields)
    except Exception as e:
        log.warning("webhook alert failed: %s", e)
    try:
        from app.core.mailer import send_mail
        detail = body + ("\n\n" + "\n".join(f"{k}: {v}" for k, v in (fields or {}).items()) if fields else "")
        for addr in _admin_emails():
            send_mail(addr, f"[IdPVault] {title}", detail)
    except Exception as e:
        log.info("email alert skipped: %s", e)


def _drift_lines(drift: dict, limit: int = 12) -> list:
    """Human-readable per-object change lines from a backup diff."""
    from app.core.events import _name
    lines = []
    # [+]/[-]/[~] prefixes — a leading "+"/"-" is a markdown bullet in
    # Slack/Mattermost and gets swallowed by the renderer.
    for rtype, ch in (drift or {}).items():
        for o in ch.get("added", []):
            lines.append(f"[+] {rtype} / {_name(o) or o.get('id', '?')}")
        for o in ch.get("removed", []):
            lines.append(f"[-] {rtype} / {_name(o) or o.get('id', '?')}")
        for c in ch.get("changed", []):
            nm = _name(c.get("after") or {}) or _name(c.get("before") or {}) or c.get("id", "?")
            lines.append(f"[~] {rtype} / {nm}")
    extra = len(lines) - limit
    out = lines[:limit]
    if extra > 0:
        out.append(f"... and {extra} more - see IdPVault -> Events")
    return out


def alert_backup_completed(tenant_name: str, snapshot_ts: str, total_objects: int,
                           drift: dict | None = None) -> None:
    """ONE alert per backup run. Drift is only ever detected during a backup, so
    change details ride in the backup email instead of a second back-to-back one.
    With changes: sent under 'drift_detected' (falls back to 'backup_success' if
    that's the subscribed category). Without: plain 'backup_success'."""
    fields = {"Tenant": tenant_name, "Snapshot": snapshot_ts, "Objects": total_objects}
    if drift:
        a = sum(len(c.get("added", [])) for c in drift.values())
        r = sum(len(c.get("removed", [])) for c in drift.values())
        ch = sum(len(c.get("changed", [])) for c in drift.values())
        fields["Changes"] = f"+{a} added, -{r} removed, ~{ch} changed"
        body = ("Backup completed and detected configuration changes vs the previous "
                "snapshot:\n\n" + "\n".join(_drift_lines(drift)))
        try:
            enabled = _enabled(_cfg())
        except Exception:
            enabled = [k for k, v in ALERT_EVENTS.items() if v["default"]]
        category = "drift_detected" if "drift_detected" in enabled else "backup_success"
        send_alert(category, f"Backup complete — changes detected — {tenant_name}",
                   body, fields)
    else:
        send_alert("backup_success", f"Backup complete — {tenant_name}",
                   "A backup completed successfully. No changes since the previous snapshot.",
                   fields)


def alert_failure(tenant_name: str, error: str) -> None:
    send_alert("backup_failed", f"Backup FAILED — {tenant_name}",
               "A backup did not complete. Check tenant credentials and IdP availability.",
               {"Tenant": tenant_name, "Error": error[:400]})


def alert_restore(tenant_name: str, kind: str, summary: dict) -> None:
    send_alert("restore_applied", f"Restore applied — {tenant_name}",
               f"A {kind} restore was applied to the live tenant.",
               {"Tenant": tenant_name, "Type": kind, "Summary": str(summary)[:400]})


def test_webhook() -> dict:
    cfg = _cfg()
    url = cfg.get("alert_webhook_url")
    if not url:
        return {"configured": False}
    fmt = _webhook_format(url, cfg.get("alert_webhook_format", "auto"))
    try:
        r = _post_webhook(url, fmt, "IdPVault test alert",
                          "This is a test alert from IdPVault. If you can see this, "
                          "your alert webhook is working.",
                          "#4d9fff", {"Status": "OK", "Source": "Settings, Send test alert"})
        return {"configured": True, "ok": r.status_code < 400,
                "status": r.status_code, "format": fmt, "body": r.text[:200]}
    except Exception as e:
        return {"configured": True, "ok": False, "error": str(e)[:200], "format": fmt}


def alert_stale(tenant_name: str, hours: float) -> None:
    send_alert("backup_stale", f"Backup OVERDUE — {tenant_name}",
               "This tenant has a schedule but no recent successful backup. The scheduler "
               "or the tenant connection may be failing silently.",
               {"Tenant": tenant_name, "Last success": f"{hours:.0f}h ago"})
