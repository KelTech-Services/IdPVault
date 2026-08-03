"""Corporate identity: names, SSO/break-glass/external flags, SCIM id.

Email becomes the sign-in identifier. It already exists as users.email, so
nothing is added for it here and NO unique constraint is created: legacy
installs carry a first-run admin with email="" (app/main.py bootstrap and the
first-run setup route), and a unique index would refuse to build on a second
empty row. Uniqueness is enforced case-insensitively in application code,
which also lets "" mean "no email set" instead of a value that collides.

Every column is nullable, so this upgrade cannot fail on existing data.

Revision ID: 0004_identity
Revises: 0003_restore_note
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_identity"
down_revision = "0003_restore_note"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("first_name", sa.String(80)),
    ("last_name", sa.String(80)),
    ("sso_user", sa.Boolean()),
    ("breakglass", sa.Boolean()),
    ("external", sa.Boolean()),
    ("scim_external_id", sa.String(200)),
)


def upgrade() -> None:
    for name, coltype in _COLUMNS:
        op.add_column("users", sa.Column(name, coltype, nullable=True))
    # Existing emails become the identifier, so normalise them now: sign-in
    # looks them up lowercased. Empty strings are left exactly as they are.
    op.execute("UPDATE users SET email = lower(email) WHERE email <> ''")
    # An MSP's client accounts (org-scoped roles) are NOT in the MSP's
    # corporate IdP - they are local accounts. Flag them external up front so
    # that turning SSO to "required" later cannot lock them out.
    op.execute("UPDATE users SET external = true "
               "WHERE role IN ('org_admin', 'org_viewer')")


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("users", name)
