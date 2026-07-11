"""Alert delivery: webhook (ntfy / Slack-compatible) + email via stored SMTP.

Called from the backup pipeline on drift detection and backup failure.
Never raises — alert failure must not break a backup run.
"""
import json
import logging

import httpx

from app.models.db import SessionLocal, Setting, User

log = logging.getLogger(__name__)


def _webhook_cfg() -> dict:
    with SessionLocal() as db:
        row = db.get(Setting, "general")
        return dict(row.value) if row else {}


def _webhook_format(url: str, fmt: str) -> str:
    """slack | ntfy. 'auto' infers from the URL (Slack/Mattermost/Discord/generic
    incoming webhooks all take Slack-compatible {"text":...} JSON)."""
    if fmt and fmt != "auto":
        return fmt
    u = (url or "").lower()
    if any(k in u for k in ("slack.com", "discord.com", "mattermost", "/hooks/", "/services/")):
        return "slack"
    return "ntfy"


def _post_webhook(url: str, fmt: str, title: str, body: str):
    if fmt == "slack":
        return httpx.post(url, json={"text": f"**{title}**\n{body}"}, timeout=10)
    return httpx.post(url, content=body.encode(),
                      headers={"Title": title, "Tags": "warning"}, timeout=10)


def _admin_emails() -> list[str]:
    with SessionLocal() as db:
        return [u.email for u in db.query(User).filter(
            User.role == "admin", User.is_active == True) if u.email]  # noqa: E712


def send_alert(title: str, body: str) -> None:
    """Fire-and-forget to webhook and admin emails. Logs, never raises."""
    cfg = {}
    try:
        cfg = _webhook_cfg()
        url = cfg.get("alert_webhook_url")
        if url:
            fmt = _webhook_format(url, cfg.get("alert_webhook_format", "auto"))
            _post_webhook(url, fmt, title, body)
    except Exception as e:
        log.warning("webhook alert failed: %s", e)
    try:
        from app.core.mailer import send_mail
        for addr in _admin_emails():
            send_mail(addr, f"[IdPVault] {title}", body)
    except Exception as e:
        log.info("email alert skipped: %s", e)


def alert_drift(tenant_name: str, snapshot_ts: str, drift: dict) -> None:
    lines = []
    for rtype, ch in drift.items():
        parts = []
        if ch.get("added"): parts.append(f"+{len(ch['added'])} added")
        if ch.get("removed"): parts.append(f"-{len(ch['removed'])} removed")
        if ch.get("changed"): parts.append(f"~{len(ch['changed'])} changed")
        lines.append(f"  {rtype}: {', '.join(parts)}")
    send_alert(f"Config drift detected — {tenant_name}",
               f"Snapshot {snapshot_ts} differs from the previous backup:\n"
               + "\n".join(lines) + "\n\nReview it in IdPVault → Events.")


def alert_failure(tenant_name: str, error: str) -> None:
    send_alert(f"Backup FAILED — {tenant_name}",
               f"The scheduled backup for {tenant_name} failed:\n\n{error}\n\n"
               f"Check tenant credentials and IdP availability in IdPVault.")


def test_webhook() -> dict:
    """Send a test alert to the configured webhook and report the real result."""
    cfg = _webhook_cfg()
    url = cfg.get("alert_webhook_url")
    if not url:
        return {"configured": False}
    fmt = _webhook_format(url, cfg.get("alert_webhook_format", "auto"))
    try:
        r = _post_webhook(url, fmt, "IdPVault test alert",
                          "This is a test alert from IdPVault. If you can see this, "
                          "your alert webhook is working.")
        return {"configured": True, "ok": r.status_code < 400,
                "status": r.status_code, "format": fmt, "body": r.text[:200]}
    except Exception as e:
        return {"configured": True, "ok": False, "error": str(e)[:200], "format": fmt}
