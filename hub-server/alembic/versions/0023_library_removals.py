"""Remember titles removed from the household library across source refreshes."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "library_removal",
        sa.Column("ref_id", sa.String(256), primary_key=True),
        sa.Column("aliases", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("library_removal")
