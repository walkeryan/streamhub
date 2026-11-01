"""Durable owner PIN credentials, scoped challenges and global attempt ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("device_code", sa.Column("owner_pin_version", sa.Integer(), nullable=True))
    op.add_column("device_code", sa.Column("approved_google_subject", sa.String(256), nullable=True))
    op.create_table("owner_credential",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("password_hash", sa.String(512)),
        sa.Column("owner_profile_id", sa.String(32)),
        sa.Column("google_subject", sa.String(256)),
        sa.Column("lease_id", sa.String(64)),
        sa.Column("lease_expires", sa.DateTime()))
    op.create_table("owner_pin_challenge",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("grant_ciphertext", sa.Text()),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("source_profile_id", sa.String(32), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("source_session_id", sa.String(64), nullable=False),
        sa.Column("target_profile_id", sa.String(32), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("google_subject", sa.String(256)),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False))
    op.create_table("owner_pin_attempt",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False))
    op.create_index("ix_owner_pin_attempt_created_at", "owner_pin_attempt", ["created_at"])


def downgrade():
    op.drop_column("device_code", "approved_google_subject")
    op.drop_column("device_code", "owner_pin_version")
    op.drop_table("owner_pin_attempt")
    op.drop_table("owner_pin_challenge")
    op.drop_table("owner_credential")
