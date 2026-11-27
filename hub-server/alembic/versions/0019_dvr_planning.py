"""Scheduled and captured DVR airing identity, retention and recovery."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("guide_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("follow_guide", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("recover_rerun", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("guide_description", sa.Text()),
        sa.Column("captured_from", sa.DateTime()),
        sa.Column("captured_until", sa.DateTime()),
        sa.Column("replacement_for", sa.String(16)),
        sa.Column("recovered_at", sa.DateTime()),
    ):
        op.add_column("recording", column)
    op.create_index("ix_recording_replacement_for", "recording", ["replacement_for"])


def downgrade() -> None:
    op.drop_index("ix_recording_replacement_for", table_name="recording")
    with op.batch_alter_table("recording") as batch:
        for name in (
            "priority",
            "protected",
            "guide_review",
            "follow_guide",
            "recover_rerun",
            "guide_description",
            "captured_from",
            "captured_until",
            "replacement_for",
            "recovered_at",
        ):
            batch.drop_column(name)
