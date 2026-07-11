"""Prometheus exposition. Token-guarded (IDPVAULT_METRICS_TOKEN), not session-auth,
so a scraper can reach it. Disabled entirely when no token is configured."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.models.db import BackupRun, SessionLocal, Snapshot, Tenant

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request) -> str:
    expected = get_settings().metrics_token
    supplied = request.query_params.get("token", "")
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        supplied = supplied or auth[7:]
    if not expected or supplied != expected:
        raise HTTPException(404, "not found")
    lines = [
        "# TYPE idpvault_tenant_info gauge",
        "# TYPE idpvault_snapshot_count gauge",
        "# TYPE idpvault_storage_bytes gauge",
        "# TYPE idpvault_last_backup_timestamp gauge",
        "# TYPE idpvault_last_backup_ok gauge",
        "# TYPE idpvault_backup_runs_total counter",
    ]
    with SessionLocal() as db:
        for t in db.query(Tenant).all():
            lbl = f'tenant="{t.slug}",provider="{t.provider}"'
            snaps = db.query(Snapshot).filter(Snapshot.tenant_id == t.id).all()
            lines.append(f"idpvault_snapshot_count{{{lbl}}} {len(snaps)}")
            lines.append(f"idpvault_storage_bytes{{{lbl}}} {sum(s.size for s in snaps)}")
            last = db.query(BackupRun).filter(BackupRun.tenant_id == t.id)\
                .order_by(BackupRun.id.desc()).first()
            if last:
                epoch = int(datetime.strptime(last.ts, "%Y%m%dT%H%M%SZ")
                            .replace(tzinfo=timezone.utc).timestamp())
                lines.append(f"idpvault_last_backup_timestamp{{{lbl}}} {epoch}")
                lines.append(f"idpvault_last_backup_ok{{{lbl}}} {1 if last.status=='ok' else 0}")
            for status in ("ok", "failed"):
                n = db.query(BackupRun).filter(BackupRun.tenant_id == t.id,
                                               BackupRun.status == status).count()
                lines.append(f'idpvault_backup_runs_total{{{lbl},status="{status}"}} {n}')
    return "\n".join(lines) + "\n"
