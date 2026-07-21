"""Add restore_runs.note (the admin's reason for a restore).

Revision ID: 0003_restore_note
Revises: 0002_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_restore_note"
down_revision = "0002_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("restore_runs", sa.Column("note", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("restore_runs", "note")
