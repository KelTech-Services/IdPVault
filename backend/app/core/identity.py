"""Identity backup + dry-run restore planning. Restore APPLY (writes) is a
separate, dedicated build — this module only reads and plans."""
import logging
import time

log = logging.getLogger(__name__)

# rough per-call default when no measured throughput exists yet (req/min, conservative)
DEFAULT_RATE_PER_MIN = 300


def run_identity_backup(tenant_id: int) -> dict:
    from app.core import crypto, storage
    from app.models.db import IdentitySnapshot, SessionLocal, Tenant
    from app.providers import get_adapter

    started = time.monotonic()
    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise ValueError("tenant not found")
        try:
            data_key = crypto.unwrap_data_key(t.wrapped_data_key)
            creds = crypto.decrypt(t.enc_credentials, data_key).decode()
            adapter = get_adapter(t.provider, t.base_url, creds)
            payload = adapter.export_identities()
            manifest = storage.write_identities(t.slug, data_key, payload)
            calls = getattr(getattr(adapter, "_rl", None), "calls", 0)
            dur = int((time.monotonic() - started) * 1000)
            db.add(IdentitySnapshot(tenant_id=t.id, ts=manifest["timestamp"],
                                    counts=manifest["counts"], size=manifest["size_encrypted"],
                                    api_calls=calls, duration_ms=dur, status="ok"))
            if t.identity_retention_keep:
                storage.prune_identities(t.slug, t.identity_retention_keep)
            db.commit()
            log.info("identity backup ok tenant=%s ts=%s calls=%s dur=%sms",
                     t.slug, manifest["timestamp"], calls, dur)
            return {"manifest": manifest, "api_calls": calls, "duration_ms": dur}
        except Exception as e:
            db.rollback()
            from datetime import datetime, timezone
            db.add(IdentitySnapshot(tenant_id=tenant_id,
                   ts=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                   status="failed", error=str(e)[:490],
                   duration_ms=int((time.monotonic() - started) * 1000)))
            db.commit()
            log.exception("identity backup failed tenant=%s", tenant_id)
            from app.core.alerts import alert_failure
            with SessionLocal() as d2:
                nm = d2.get(Tenant, tenant_id)
                alert_failure((nm.name if nm else str(tenant_id)) + " (identity)", str(e)[:490])
            raise


def _uid(o: dict) -> str:
    return str(o.get("id") or o.get("pk") or o.get("username") or "")


def plan_identity_restore(tenant_id: int, snapshot_ts: str, actor: str) -> dict:
    """Dry-run: what a restore WOULD do. Read-only."""
    import json
    from app.core import crypto, storage
    from app.models.db import AuditLog, SessionLocal, Tenant
    from app.providers import get_adapter

    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise ValueError("tenant not found")
        data_key = crypto.unwrap_data_key(t.wrapped_data_key)
        creds = crypto.decrypt(t.enc_credentials, data_key).decode()
        adapter = get_adapter(t.provider, t.base_url, creds)

        snap = storage.read_identities(t.slug, snapshot_ts, data_key)
        live = adapter.export_identities()

        live_users = {_uid(u): u for u in live.get("users", [])}
        users_plan = []
        for u in snap.get("users", []):
            uid = _uid(u)
            lv = live_users.get(uid)
            if lv is None:
                users_plan.append({"user_id": uid, "action": "recreate"})
            elif json.dumps(u, sort_keys=True) != json.dumps(lv, sort_keys=True):
                users_plan.append({"user_id": uid, "action": "update"})
            else:
                users_plan.append({"user_id": uid, "action": "identical"})

        def _edge_set(rows, keys):
            return {tuple(str(r.get(k)) for k in keys) for r in rows}
        mem_add = _edge_set(snap.get("group_memberships", []), ("group_id", "user_id")) \
            - _edge_set(live.get("group_memberships", []), ("group_id", "user_id"))
        ag_add = _edge_set(snap.get("app_group_assignments", []), ("app_id", "group_id")) \
            - _edge_set(live.get("app_group_assignments", []), ("app_id", "group_id"))
        au_add = _edge_set(snap.get("app_user_assignments_direct", []), ("app_id", "user_id")) \
            - _edge_set(live.get("app_user_assignments_direct", []), ("app_id", "user_id"))

        recreate = sum(1 for u in users_plan if u["action"] == "recreate")
        summary = {
            "users": {"recreate": recreate,
                      "update": sum(1 for u in users_plan if u["action"] == "update"),
                      "identical": sum(1 for u in users_plan if u["action"] == "identical")},
            "group_memberships_to_add": len(mem_add),
            "app_group_assignments_to_add": len(ag_add),
            "app_user_assignments_direct_to_add": len(au_add),
        }
        manual_steps = []
        if recreate:
            manual_steps.append(f"{recreate} recreated user(s) will need a PASSWORD RESET "
                                f"(credentials are never exportable via the IdP API).")
            manual_steps.append("Recreated users will need to RE-ENROLL MFA factors.")
        if t.provider == "authentik":
            manual_steps.append("For full credential recovery on self-hosted Authentik, use the "
                                "encrypted pg_dump from full-DR mode instead of API user restore.")

        db.add(AuditLog(actor=actor, action="identity.restore.preview",
                        detail={"tenant": t.slug, "snapshot": snapshot_ts}))
        db.commit()
        return {"summary": summary, "manual_steps": manual_steps,
                "note": "Provenance-preserving: group-inherited access is restored via group "
                        "memberships + group→app assignments; only genuinely DIRECT user→app "
                        "assignments are recreated as direct. Apply (write) is a separate step."}


def apply_identity_restore(tenant_id: int, snapshot_ts: str, actor: str) -> dict:
    """Write path: recreate missing users + re-add missing edges. Additive,
    idempotent, per-object reporting. Persists a RestoreRun report."""
    from app.core import crypto, storage
    from app.models.db import AuditLog, RestoreRun, SessionLocal, Tenant
    from app.providers import get_adapter

    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise ValueError("tenant not found")
        data_key = crypto.unwrap_data_key(t.wrapped_data_key)
        creds = crypto.decrypt(t.enc_credentials, data_key).decode()
        adapter = get_adapter(t.provider, t.base_url, creds)
        snap = storage.read_identities(t.slug, snapshot_ts, data_key)
        report = adapter.apply_identities(snap)  # may raise NotImplementedError

        summary = {}
        for cat, r in report.items():
            summary[cat] = {k: (len(v) if isinstance(v, list) else v) for k, v in r.items()}
        created = report["users"]["created"]
        manual = []
        if created:
            manual.append(f"{created} user(s) recreated — send PASSWORD RESET / activation "
                          f"(credentials are not restorable via API).")
            manual.append(f"{created} recreated user(s) must RE-ENROLL MFA.")

        run = RestoreRun(tenant_id=t.id, snapshot_ts=snapshot_ts, mode="identity_apply",
                         actor=actor, summary=summary,
                         results={"report": report, "manual_steps": manual})
        db.add(run)
        db.add(AuditLog(actor=actor, action="identity.restore.apply",
                        detail={"tenant": t.slug, "snapshot": snapshot_ts,
                                "users_created": created}))
        db.commit()
        return {"restore_run_id": run.id, "summary": summary, "manual_steps": manual}


def estimate_next(tenant_id: int) -> dict:
    """Duration estimate for the next identity backup + a cadence recommendation."""
    from app.core.security import require_admin  # noqa (import guard parity)
    from app.models.db import IdentitySnapshot, SessionLocal, Tenant

    with SessionLocal() as db:
        t = db.get(Tenant, tenant_id)
        if t is None:
            raise ValueError("tenant not found")
        last = db.query(IdentitySnapshot).filter(
            IdentitySnapshot.tenant_id == tenant_id,
            IdentitySnapshot.status == "ok").order_by(IdentitySnapshot.id.desc()).first()
        if last and last.api_calls and last.duration_ms:
            secs = round(last.duration_ms / 1000)
            basis = "measured"
            calls = last.api_calls
        else:
            basis = "rough (no measured run yet)"
            calls = None
            secs = None
        rec = "Daily is fine."
        if secs is not None and secs > 900:
            rec = ("Backup exceeds ~15 min — schedule off-hours, consider a Workforce "
                   "rate-limit multiplier or support increase, and confirm daily is needed.")
        return {"basis": basis, "last_duration_s": secs, "last_api_calls": calls,
                "recommendation": rec}
