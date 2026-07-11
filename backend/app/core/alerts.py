"""Alert delivery: webhook (ntfy / Slack-compatible) + email via stored SMTP.

Called from the backup pipeline on drift detection and backup failure.
Never raises — alert failure must not break a backup run.
"""
import json
import logging

import httpx

from app.models.db import SessionLocal, Setting, User

log = logging.getLogger(__name__)


def _webhook_url() -> str | None:
    with SessionLocal() as db:
        row = db.get(Setting, "general")
        return (row.value.get("alert_webhook_url") or None) if row else None


def _admin_emails() -> list[str]:
    with SessionLocal() as db:
        return [u.email for u in db.query(User).filter(
            User.role == "admin", User.is_active == True) if u.email]  # noqa: E712


def send_alert(title: str, body: str) -> None:
    """Fire-and-forget to webhook and admin emails. Logs, never raises."""
    url = None
    try:
        url = _webhook_url()
        if url:
            if "slack.com" in url or "discord.com" in url:
                payload = {"text": f"*{title}*\n{body}"}
                httpx.post(url, json=payload, timeout=10)
            else:  # ntfy-style: title header + plain body
                httpx.post(url, content=body.encode(),
                           headers={"Title": title, "Tags": "warning"}, timeout=10)
    except Exception as e:
        log.warning("webhook alert failed (%s): %s", url, e)
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
