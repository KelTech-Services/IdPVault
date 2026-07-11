"""SMTP delivery using stored settings. Config lives in the settings table;
the SMTP password is encrypted with the master key."""
import smtplib
from email.message import EmailMessage

from app.core import crypto
from app.models.db import SessionLocal, Setting


def get_smtp_config() -> dict | None:
    with SessionLocal() as db:
        row = db.get(Setting, "smtp")
        if not row or not row.value.get("host"):
            return None
        cfg = dict(row.value)
        if cfg.get("password_enc"):
            cfg["password"] = crypto.decrypt(bytes.fromhex(cfg["password_enc"]), crypto._master_key()).decode()
        return cfg


def send_mail(to: str, subject: str, body: str) -> None:
    cfg = get_smtp_config()
    if cfg is None:
        raise RuntimeError("SMTP is not configured")
    msg = EmailMessage()
    msg["From"] = cfg.get("from_addr") or cfg["username"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(cfg.get("port") or 587)
    if cfg.get("tls_mode") == "ssl":
        server = smtplib.SMTP_SSL(cfg["host"], port, timeout=15)
    else:
        server = smtplib.SMTP(cfg["host"], port, timeout=15)
        if cfg.get("tls_mode", "starttls") == "starttls":
            server.starttls()
    try:
        if cfg.get("username"):
            server.login(cfg["username"], cfg.get("password", ""))
        server.send_message(msg)
    finally:
        server.quit()
