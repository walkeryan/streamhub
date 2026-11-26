"""Profile channel lineup and optional recording rule padding."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_channel",
        sa.Column("profile_id", sa.String(32), sa.ForeignKey("profile.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channel.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position", sa.Integer()),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("recording_rule", sa.Column("pad_before_s", sa.Integer()))
    op.add_column("recording_rule", sa.Column("pad_after_s", sa.Integer()))


def downgrade() -> None:
    with op.batch_alter_table("recording_rule") as batch:
        batch.drop_column("pad_after_s")
        batch.drop_column("pad_before_s")
    op.drop_table("profile_channel")
