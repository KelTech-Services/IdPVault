"""SCIM provisioning: push groups, their membership, and role mapping.

Push groups are IdP-owned directory groups. They are a NEW table rather than
a reuse of orgs: orgs are MSP client companies (they own tenants and scope
org_admin/org_viewer users), and letting an IdP write into that structure
would hand directory admins control of MSP client scoping.

Revision ID: 0005_scim
Revises: 0004_identity
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_scim"
down_revision = "0004_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("scim_external_id", sa.String(200), nullable=True),
        sa.Column("scim_role", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_push_groups_scim_external_id", "push_groups",
                    ["scim_external_id"])
    op.create_table(
        "push_group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("push_groups.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_push_group_members_group_id", "push_group_members",
                    ["group_id"])
    op.create_index("ix_push_group_members_user_id", "push_group_members",
                    ["user_id"])


def downgrade() -> None:
    op.drop_table("push_group_members")
    op.drop_table("push_groups")
