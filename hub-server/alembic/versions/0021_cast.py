"""Paired receivers, command acknowledgments and per-profile recommendations."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cast_target",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "sid", sa.String(64), sa.ForeignKey("auth_session.sid", ondelete="CASCADE"), nullable=False, unique=True
        ),
        sa.Column("profile_id", sa.String(), sa.ForeignKey("profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("seen", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cast_target_profile_id", "cast_target", ["profile_id"])
    op.create_table(
        "cast_connection",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "target_id", sa.String(36), sa.ForeignKey("cast_target.id", ondelete="CASCADE"), nullable=False, unique=True
        ),
        sa.Column(
            "sid", sa.String(64), sa.ForeignKey("auth_session.sid", ondelete="CASCADE"), nullable=False, unique=True
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("connected", sa.Boolean(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("playback", sa.JSON(), nullable=False),
        sa.Column("commands", sa.JSON(), nullable=False),
        sa.Column("last_ack", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(160)),
    )
    op.create_table(
        "recommendation_vote",
        sa.Column("profile_id", sa.String(), sa.ForeignKey("profile.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ref_id", sa.String(256), primary_key=True),
        sa.Column("vote", sa.String(4), nullable=False),
        sa.Column("genres", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("recommendation_vote")
    op.drop_table("cast_connection")
    op.drop_table("cast_target")
