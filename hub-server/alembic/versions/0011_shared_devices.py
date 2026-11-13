"""Shared devices: a pairing can ask for one, and a session counts its hand-overs."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN with server defaults: existing sessions and pending pairings stay valid.
    op.add_column("auth_session", sa.Column("generation", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("device_code", sa.Column("shared_device", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("device_code", sa.Column("approved_by_user_id", sa.String(32)))


def downgrade() -> None:
    with op.batch_alter_table("device_code") as batch:
        batch.drop_column("approved_by_user_id")
        batch.drop_column("shared_device")
    with op.batch_alter_table("auth_session") as batch:
        batch.drop_column("generation")
