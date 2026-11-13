"""Media requests and per-profile notices."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
# Numbered ahead of the catalog branch's revisions; the chain is joined when the branches merge.
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_request",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("profile_id", sa.String(32), sa.ForeignKey("profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_type", sa.String(8), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("year", sa.Integer()),
        sa.Column("poster", sa.Text()),
        sa.Column("seasons", sa.JSON()),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("manager_request_id", sa.Integer()),
        sa.Column("decided_by_profile_id", sa.String(32)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_media_request_profile_id", "media_request", ["profile_id"])
    op.create_index("ix_media_request_state", "media_request", ["state"])
    op.create_index(
        "uq_media_request_open",
        "media_request",
        ["media_type", "tmdb_id"],
        unique=True,
        sqlite_where=sa.text("state IN ('pending_approval', 'approved', 'sent')"),
    )
    op.create_table(
        "notice",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("profile_id", sa.String(32), sa.ForeignKey("profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.String(256)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime()),
    )
    op.create_index("ix_notice_profile_id", "notice", ["profile_id"])


def downgrade() -> None:
    op.drop_table("notice")
    op.drop_table("media_request")
