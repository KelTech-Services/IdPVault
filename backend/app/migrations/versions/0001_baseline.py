"""v1.2.3 baseline.

Represents the schema exactly as shipped in v1.2.3, which is produced by
Base.metadata.create_all() plus the frozen legacy reconcile lists in
app.models.db (_COLUMN_MIGRATIONS / _TYPE_MIGRATIONS).

Intentionally a no-op: databases are STAMPED at this revision, never
migrated to it. Fresh installs are created via create_all and stamped at
head; pre-Alembic installs are reconciled to the baseline and stamped here.
"""
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
