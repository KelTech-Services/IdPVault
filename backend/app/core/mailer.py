"""SMTP delivery using stored settings. Sends branded multipart HTML email
(with the IdPVault logo inline) plus a plain-text fallback. Every email in the
app goes through send_mail, so all types (alerts, invites, resets) are branded.
"""
import html as _html
import os
import re
import smtplib
from email.message import EmailMessage

from app.core import crypto
from app.models.db import SessionLocal, Setting

_LOGO_CANDIDATES = [
    os.path.join(os.getcwd(), "frontend", "IdPVault_logo.png"),
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "frontend", "IdPVault_logo.png"),
]


def get_smtp_config() -> dict | None:
    with SessionLocal() as db:
        row = db.get(Setting, "smtp")
        if not row or not row.value.get("host"):
            return None
        cfg = dict(row.value)
        if cfg.get("password_enc"):
            cfg["password"] = crypto.decrypt(bytes.fromhex(cfg["password_enc"]), crypto._master_key()).decode()
        return cfg


def _logo_bytes() -> bytes | None:
    for p in _LOGO_CANDIDATES:
        try:
            with open(p, "rb") as f:
                return f.read()
        except Exception:
            continue
    return None


def _html_body(subject: str, body: str) -> str:
    safe = _html.escape(body)
    safe = re.sub(r'(https?://[^\s]+)',
                  r'<a href="\1" style="color:#2f6fed;word-break:break-all">\1</a>', safe)
    safe = safe.replace("\n", "<br>")
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#eef1f5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef1f5;">
<tr><td align="center" style="padding:30px 14px;">
  <table role="presentation" width="520" cellpadding="0" cellspacing="0" style="max-width:520px;width:100%;background:#ffffff;border:1px solid #e2e7ef;border-radius:14px;overflow:hidden;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <tr><td style="padding:26px 30px 6px;"><img src="cid:idpvaultlogo" alt="IdPVault" height="34" style="height:34px;"></td></tr>
    <tr><td style="padding:6px 30px 2px;"><div style="font-size:18px;font-weight:650;color:#141c28;">{_html.escape(subject)}</div></td></tr>
    <tr><td style="padding:10px 30px 26px;color:#3b4757;font-size:14px;line-height:1.65;">{safe}</td></tr>
    <tr><td style="padding:16px 30px;background:#f6f8fb;border-top:1px solid #eceff4;color:#93a0b1;font-size:12px;">IdPVault - self-hosted identity backup &amp; restore</td></tr>
  </table>
  <div style="color:#aeb8c6;font-size:11px;padding-top:14px;">This is an automated message from IdPVault.</div>
</td></tr></table></body></html>"""


def send_mail(to: str, subject: str, body: str) -> None:
    cfg = get_smtp_config()
    if cfg is None:
        raise RuntimeError("SMTP is not configured")
    msg = EmailMessage()
    msg["From"] = cfg.get("from_addr") or cfg["username"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)                                  # plain-text fallback
    msg.add_alternative(_html_body(subject, body), subtype="html")
    logo = _logo_bytes()
    if logo:
        try:
            msg.get_payload()[1].add_related(logo, maintype="image", subtype="png", cid="idpvaultlogo")
        except Exception:
            pass
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
