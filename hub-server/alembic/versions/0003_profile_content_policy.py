"""Add disabled-by-default profile policy storage and session revocation ledger."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("device_code", sa.Column("owner_auth_time", sa.Float(), nullable=True))
    op.add_column("device_code", sa.Column("approved_by_profile_id", sa.String(32), nullable=True))
    op.add_column("device_code", sa.Column("approved_by_version", sa.Integer(), nullable=True))
    op.add_column("device_code", sa.Column("approved_by_session_id", sa.String(64), nullable=True))
    op.add_column("profile", sa.Column("content_policy", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "revoked_session",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    for name in ("owner_auth_time", "approved_by_profile_id", "approved_by_version", "approved_by_session_id"):
        op.drop_column("device_code", name)
    op.drop_table("revoked_session")
    op.drop_column("profile", "content_policy")
