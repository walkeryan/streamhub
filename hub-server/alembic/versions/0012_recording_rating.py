"""Recordings keep the rating evidence of the programme they were scheduled from."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing recordings have no evidence: unknown, which only a user allowed unrated titles sees.
    op.add_column("recording", sa.Column("age_evidence", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("recording") as batch:
        batch.drop_column("age_evidence")
