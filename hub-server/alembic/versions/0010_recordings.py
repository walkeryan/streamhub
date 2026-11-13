"""Recording rules and recordings."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording_rule",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("profile_id", sa.String(32), sa.ForeignKey("profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("match", sa.String(256), nullable=False),
        sa.Column("channel_id", sa.String(256)),
        sa.Column("new_only", sa.Boolean(), nullable=False),
        sa.Column("keep", sa.Integer()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_recording_rule_profile_id", "recording_rule", ["profile_id"])
    op.create_table(
        "recording",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("profile_id", sa.String(32), sa.ForeignKey("profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.String(16), sa.ForeignKey("recording_rule.id", ondelete="SET NULL")),
        sa.Column("channel_id", sa.String(256), nullable=False),
        sa.Column("channel_name", sa.String(256), nullable=False),
        sa.Column("programme_key", sa.String(320), nullable=False, unique=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("subtitle", sa.String(512)),
        sa.Column("season", sa.Integer()),
        sa.Column("episode", sa.Integer()),
        sa.Column("is_sport", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("pad_before_s", sa.Integer(), nullable=False),
        sa.Column("pad_after_s", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("path", sa.Text()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_recording_profile_id", "recording", ["profile_id"])
    op.create_index("ix_recording_rule_id", "recording", ["rule_id"])
    op.create_index("ix_recording_starts_at", "recording", ["starts_at"])
    op.create_index("ix_recording_state", "recording", ["state"])


def downgrade() -> None:
    op.drop_table("recording")
    op.drop_table("recording_rule")
