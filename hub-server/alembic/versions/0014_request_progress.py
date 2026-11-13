"""What the request manager last said about a request: its phase, when it was last asked and
when it may be asked again."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: an existing request has no stored answer and is asked about on the next pass.
    op.add_column("media_request", sa.Column("progress", sa.JSON()))
    op.add_column("media_request", sa.Column("progress_checked_at", sa.DateTime()))
    op.add_column("media_request", sa.Column("progress_changed_at", sa.DateTime()))
    op.add_column("media_request", sa.Column("poll_after", sa.DateTime()))
    op.add_column("media_request", sa.Column("poll_failures", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("media_request") as batch:
        batch.drop_column("poll_failures")
        batch.drop_column("poll_after")
        batch.drop_column("progress_changed_at")
        batch.drop_column("progress_checked_at")
        batch.drop_column("progress")
