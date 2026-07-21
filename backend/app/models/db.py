from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(20))  # authentik | okta | auth0
    base_url: Mapped[str] = mapped_column(String(255))
    enc_credentials: Mapped[bytes] = mapped_column(LargeBinary)   # AES-GCM w/ data key
    wrapped_data_key: Mapped[bytes] = mapped_column(LargeBinary)  # data key wrapped by master
    enc_db_url: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # full-DR pg_dump source
    db_dump_exclude_events: Mapped[bool] = mapped_column(Boolean, default=False)  # full-DR: skip event-log rows
    schedule_cron: Mapped[str | None] = mapped_column(String(60), nullable=True)
    retention_keep: Mapped[int] = mapped_column(Integer, default=30)
    identity_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_schedule_cron: Mapped[str | None] = mapped_column(String(60), nullable=True)
    identity_retention_keep: Mapped[int] = mapped_column(Integer, default=14)
    org_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # MSP: client org
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TenantState(Base):
    """Live-state poll cache (v1.2): per-category counts + drift vs the latest
    snapshot, refreshed by the scheduler sweep or a manual refresh."""
    __tablename__ = "tenant_state"

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class Org(Base):
    """MSP client organization: light CRM (contact + notes + billing memo),
    the grouping key for tenants, and the scope for org_admin/org_viewer users."""
    __tablename__ = "orgs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    contact_name: Mapped[str] = mapped_column(String(120), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    contact_phone: Mapped[str] = mapped_column(String(60), default="")
    notes: Mapped[str] = mapped_column(String(4000), default="")
    billing_memo: Mapped[str] = mapped_column(String(200), default="")   # what the MSP charges
    billing_cadence: Mapped[str] = mapped_column(String(10), default="")  # monthly | annual | ""
    renewal_date: Mapped[str] = mapped_column(String(10), default="")     # YYYY-MM-DD or ""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    ts: Mapped[str] = mapped_column(String(20), index=True)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    size: Mapped[int] = mapped_column(Integer, default=0)
    drift: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    actor: Mapped[str] = mapped_column(String(120), default="system")
    action: Mapped[str] = mapped_column(String(120))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


# FROZEN AT v1.2.3 - never extend these lists. They exist only to reconcile
# pre-Alembic installs up to the v1.2.3 baseline schema; all schema changes
# after v1.2.3 are Alembic revisions in app/migrations/versions/.
_COLUMN_MIGRATIONS = [
    ("tenants", "enc_db_url", "BYTEA"),
    ("tenants", "identity_enabled", "BOOLEAN DEFAULT FALSE"),
    ("tenants", "identity_schedule_cron", "VARCHAR(60)"),
    ("tenants", "identity_retention_keep", "INTEGER DEFAULT 14"),
    ("users", "mfa_enabled", "BOOLEAN DEFAULT FALSE"),
    ("users", "mfa_secret_enc", "VARCHAR(255)"),
    ("users", "time_format", "VARCHAR(6) DEFAULT 'auto'"),
    ("users", "theme", "VARCHAR(10) DEFAULT 'dark'"),
    ("users", "failed_logins", "INTEGER DEFAULT 0"),
    ("users", "locked_until", "TIMESTAMPTZ"),
    ("tenants", "org_id", "INTEGER"),
    ("tenants", "db_dump_exclude_events", "BOOLEAN DEFAULT FALSE"),
    ("users", "org_id", "INTEGER"),
    ("backup_runs", "trigger", "VARCHAR(12)"),
]


# Idempotent column-type widenings for columns that outgrew their original length.
_TYPE_MIGRATIONS = [
    ("restore_runs", "mode", "VARCHAR(20)"),
    ("users", "role", "VARCHAR(20)"),   # org_admin / org_viewer need > 10 chars
]


# Tables that existed at the v1.2.3 Alembic baseline. Pre-Alembic installs
# are reconciled to exactly this set before being stamped at the baseline;
# tables added after v1.2.3 arrive via Alembic revisions only.
_BASELINE_TABLES = [
    "tenants", "tenant_state", "orgs", "snapshots", "audit_log", "users",
    "auth_sessions", "mfa_trusts", "settings", "backup_runs", "events",
    "identity_snapshots", "restore_runs",
]

_ALEMBIC_BASELINE = "0001_baseline"


def _legacy_reconcile() -> None:
    """Bring a pre-Alembic database up to the v1.2.3 baseline. Frozen."""
    from sqlalchemy import text
    with engine.begin() as conn:
        for table, col, coltype in _COLUMN_MIGRATIONS:
            conn.execute(text(
                f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {coltype}'))
        for table, col, coltype in _TYPE_MIGRATIONS:
            conn.execute(text(f'ALTER TABLE {table} ALTER COLUMN {col} TYPE {coltype}'))


def _alembic_cfg():
    import os
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option(
        "script_location",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "migrations"))
    return cfg


def init_db() -> None:
    """Boot-time schema management.

    Three paths:
    - Fresh install: create_all() builds the full current schema, stamp head
      (no revisions run - the models are authoritative for new databases).
    - Pre-Alembic install (<= v1.2.3): reconcile to exactly the v1.2.3
      baseline, stamp the baseline revision, then upgrade to head.
    - Alembic-managed install: upgrade to head.
    """
    from alembic import command
    from sqlalchemy import inspect
    insp = inspect(engine)
    fresh = not insp.has_table("users")
    cfg = _alembic_cfg()
    if fresh:
        Base.metadata.create_all(engine)
        _legacy_reconcile()
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.stamp(cfg, "head")
        return
    if not insp.has_table("alembic_version"):
        baseline = [Base.metadata.tables[t] for t in _BASELINE_TABLES]
        Base.metadata.create_all(engine, tables=baseline)
        _legacy_reconcile()
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.stamp(cfg, _ALEMBIC_BASELINE)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(300), default="")  # scrypt salt$hash
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin | user | org_admin | org_viewer
    org_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # scope for org_* roles
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    invite_token: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret_enc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    time_format: Mapped[str] = mapped_column(String(6), default="auto")  # auto | 12 | 24
    theme: Mapped[str] = mapped_column(String(10), default="dark")  # dark | light
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MfaTrust(Base):
    __tablename__ = "mfa_trusts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class BackupRun(Base):
    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    ts: Mapped[str] = mapped_column(String(20), index=True)     # snapshot ts (or attempt time on failure)
    status: Mapped[str] = mapped_column(String(10))              # ok | failed
    trigger: Mapped[str | None] = mapped_column(String(12), nullable=True)  # manual | scheduled
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    snapshot_ts: Mapped[str] = mapped_column(String(20), index=True)
    event_type: Mapped[str] = mapped_column(String(10), index=True)   # add | update | delete
    resource_type: Mapped[str] = mapped_column(String(60), index=True)
    object_id: Mapped[str] = mapped_column(String(120), default="")
    object_name: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class IdentitySnapshot(Base):
    __tablename__ = "identity_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    ts: Mapped[str] = mapped_column(String(20), index=True)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)      # users/memberships/etc
    size: Mapped[int] = mapped_column(Integer, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)    # measured, for estimates
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="ok")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RestoreRun(Base):
    __tablename__ = "restore_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    snapshot_ts: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20))  # dry_run | apply | identity_apply
    actor: Mapped[str] = mapped_column(String(120), default="system")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)  # admin's reason (applies)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)   # counts per action/status
    results: Mapped[dict] = mapped_column(JSON, default=dict)   # {"items": [...]} per-object detail
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Job(Base):
    """Background job row (see app.core.jobs). One per backup/restore run,
    manual or scheduled - the UI's nav activity area polls these."""
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(20))    # config_backup | identity_backup | identity_restore
    trigger: Mapped[str] = mapped_column(String(12), default="manual")   # manual | scheduled
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)  # queued | running | ok | failed
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # params in; trimmed result / error out
    requested_by: Mapped[str] = mapped_column(String(80), default="scheduler")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
