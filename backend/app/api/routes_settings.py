"""App settings — admin only. SMTP password encrypted with the master key."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core import crypto
from app.core.security import require_admin
from app.models.db import AuditLog, SessionLocal, Setting

router = APIRouter(tags=["settings"], dependencies=[Depends(require_admin)])


class SmtpIn(BaseModel):
    host: str = ""
    port: int = 587
    tls_mode: str = "starttls"   # starttls | ssl | none
    username: str = ""
    password: str | None = None  # provide to change; omit to keep
    from_addr: str = ""


class SettingsIn(BaseModel):
    smtp: SmtpIn | None = None
    alert_webhook_url: str | None = None
    alert_webhook_format: str | None = None  # auto | slack | ntfy
    alert_events: list | None = None  # subscribed alert categories
    default_schedule_cron: str | None = None
    default_identity_schedule_cron: str | None = None
    default_retention_keep: int | None = None
    org_timezone: str | None = None  # IANA tz that cron schedules run in (default UTC)
    okta_rate_reserve_pct: int | None = None  # 0-90; headroom left on Okta limits
    mfa_trust_days: int | None = None  # 0 = always prompt for MFA; N = trust device N days
    login_max_attempts: int | None = None    # failed logins before lockout (default 5)
    login_lockout_minutes: int | None = None  # lockout duration (default 15)
    stale_backup_hours: int | None = None     # alert if scheduled backup older than this (default 26)
    public_url: str | None = None             # canonical public URL (links, https, host enforcement)
    enforce_host: bool | None = None          # reject requests whose Host != public_url host


def _get(db, key: str) -> dict:
    row = db.get(Setting, key)
    return dict(row.value) if row else {}


def _put(db, key: str, value: dict) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


@router.get("/settings")
def get_settings_api() -> dict:
    with SessionLocal() as db:
        smtp = _get(db, "smtp")
        smtp.pop("password_enc", None)
        smtp["password_set"] = bool(_get(db, "smtp").get("password_enc"))
        general = _get(db, "general")
        return {"smtp": smtp, **general}


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request) -> dict:
    if body.org_timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(body.org_timezone)
        except Exception:
            raise HTTPException(422, f"unknown timezone: {body.org_timezone}")
    tz_changed = False
    with SessionLocal() as db:
        if body.smtp is not None:
            cur = _get(db, "smtp")
            new = body.smtp.model_dump(exclude={"password"})
            if body.smtp.password:
                new["password_enc"] = crypto.encrypt(
                    body.smtp.password.encode(), crypto._master_key()).hex()
            elif cur.get("password_enc"):
                new["password_enc"] = cur["password_enc"]
            _put(db, "smtp", new)
        general = _get(db, "general")
        for k in ("alert_webhook_url", "alert_webhook_format", "alert_events", "default_schedule_cron", "default_identity_schedule_cron", "default_retention_keep", "org_timezone", "okta_rate_reserve_pct", "mfa_trust_days", "login_max_attempts", "login_lockout_minutes", "stale_backup_hours", "public_url", "enforce_host"):
            v = getattr(body, k)
            if v is not None:
                if k == "org_timezone" and v != general.get("org_timezone", "UTC"):
                    tz_changed = True
                general[k] = v
        _put(db, "general", general)
        db.add(AuditLog(actor=request.state.user["username"], action="settings.update", detail={}))
        db.commit()
    if tz_changed:
        # Re-register all tenant cron jobs so they run in the new timezone.
        from app.core.scheduler import load_tenant_jobs
        load_tenant_jobs()
    return {"ok": True}


class TestMailIn(BaseModel):
    to: str


@router.post("/settings/test-alert")
def test_alert() -> dict:
    from app.core.alerts import test_webhook
    r = test_webhook()
    if not r.get("configured"):
        raise HTTPException(422, "no alert webhook configured")
    if not r.get("ok"):
        import logging
        logging.getLogger(__name__).warning("test webhook failed: %s", r.get("error") or r.get("status"))
        # build the response ONLY from untainted values (int cast / constant match)
        try:
            status = int(r.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        fmt = next((x for x in ("slack", "ntfy", "discord", "mattermost", "auto")
                    if x == r.get("format")), "unknown")
        detail = (f"webhook returned HTTP {status}" if status
                  else "webhook connection failed (details in server logs)")
        raise HTTPException(502, f"{detail} (format: {fmt})")
    return r


@router.post("/settings/test-email")
def test_email(body: TestMailIn) -> dict:
    from app.core.mailer import send_mail
    try:
        send_mail(body.to, "IdPVault test email",
                  "SMTP is configured correctly — this is a test message from IdPVault.")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("test email failed: %s", e)
        raise HTTPException(502, "send failed - check host/port/credentials (details in server logs)")
    return {"sent": True}
