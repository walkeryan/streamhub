"""Separate locally approved recovery attempts from old credential guessing budgets."""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "owner_pin_attempt", sa.Column("credential_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("owner_pin_attempt", sa.Column("purpose", sa.String(16), nullable=False, server_default="verify"))
    op.add_column("owner_pin_attempt", sa.Column("challenge_id", sa.String(64), nullable=True))
    # Preserve audit records and apply existing attempts to the current credential.
    op.execute(
        "UPDATE owner_pin_attempt SET credential_version = COALESCE((SELECT credential_version FROM owner_credential WHERE id=1),0)"
    )


def downgrade():
    for name in ("challenge_id", "purpose", "credential_version"):
        op.drop_column("owner_pin_attempt", name)
