"""Preserve explicit broadcast status from the programme guide."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("programme", sa.Column("broadcast_kind", sa.String(12), nullable=True))


def downgrade() -> None:
    op.drop_column("programme", "broadcast_kind")
