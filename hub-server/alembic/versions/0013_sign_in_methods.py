"""More ways to sign in: a Plex identity and a username with a password on the user, named
invites, and sessions that count their refreshes."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN, all nullable: every existing user keeps exactly the sign-in they had.
    op.add_column("user", sa.Column("plex_account_id", sa.String(64)))
    op.add_column("user", sa.Column("plex_username", sa.String(128)))
    op.add_column("user", sa.Column("username", sa.String(32)))
    op.add_column("user", sa.Column("password_hash", sa.String(256)))
    # SQLite cannot add a UNIQUE column; a unique index says the same and ignores the NULLs.
    op.create_index("ux_user_plex_account_id", "user", ["plex_account_id"], unique=True)
    op.create_index("ux_user_username", "user", ["username"], unique=True)
    # An invite may name the person it is for, or be the setup link of one existing user.
    op.add_column("invite", sa.Column("display_name", sa.String(64)))
    op.add_column("invite", sa.Column("user_id", sa.String(32)))
    op.create_index("ix_invite_user_id", "invite", ["user_id"])
    # Refresh tokens rotate: which one is current, and when it became so. Existing sessions start at 0,
    # which is what a token minted before this carries.
    op.add_column("auth_session", sa.Column("refresh_seq", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("auth_session", sa.Column("refresh_rotated_at", sa.DateTime()))


def downgrade() -> None:
    with op.batch_alter_table("auth_session") as batch:
        batch.drop_column("refresh_rotated_at")
        batch.drop_column("refresh_seq")
    op.drop_index("ix_invite_user_id", table_name="invite")
    with op.batch_alter_table("invite") as batch:
        batch.drop_column("user_id")
        batch.drop_column("display_name")
    op.drop_index("ux_user_username", table_name="user")
    op.drop_index("ux_user_plex_account_id", table_name="user")
    with op.batch_alter_table("user") as batch:
        batch.drop_column("password_hash")
        batch.drop_column("username")
        batch.drop_column("plex_username")
        batch.drop_column("plex_account_id")
