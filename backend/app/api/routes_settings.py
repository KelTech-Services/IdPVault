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
    default_schedule_cron: str | None = None
    default_retention_keep: int | None = None
    okta_rate_reserve_pct: int | None = None  # 0-90; headroom left on Okta limits


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
        for k in ("alert_webhook_url", "default_schedule_cron", "default_retention_keep", "okta_rate_reserve_pct"):
            v = getattr(body, k)
            if v is not None:
                general[k] = v
        _put(db, "general", general)
        db.add(AuditLog(actor=request.state.user["username"], action="settings.update", detail={}))
        db.commit()
    return {"ok": True}


class TestMailIn(BaseModel):
    to: str


@router.post("/settings/test-email")
def test_email(body: TestMailIn) -> dict:
    from app.core.mailer import send_mail
    try:
        send_mail(body.to, "IdPVault test email",
                  "SMTP is configured correctly — this is a test message from IdPVault.")
    except Exception as e:
        raise HTTPException(502, f"send failed: {e}")
    return {"sent": True}
