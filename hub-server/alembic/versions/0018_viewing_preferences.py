"""Per-profile Continue Watching visibility, watchlist availability, and edition choice."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("progress", sa.Column("hidden_from_continue", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("watchlist", sa.Column("last_playable", sa.Boolean()))
    op.add_column("watchlist", sa.Column("playable_since", sa.DateTime()))
    op.create_table(
        "playback_preference",
        sa.Column("profile_id", sa.String(), sa.ForeignKey("profile.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ref_id", sa.String(256), primary_key=True),
        sa.Column("variant_id", sa.String(256), nullable=False),
        sa.Column("audio", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("playback_preference")
    with op.batch_alter_table("watchlist") as batch:
        batch.drop_column("last_playable")
        batch.drop_column("playable_since")
    with op.batch_alter_table("progress") as batch:
        batch.drop_column("hidden_from_continue")
