"""Whether a profile's PIN was chosen as an arrow pattern, and how many arrows long it is."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: every PIN set before this was typed as digits, which is what null says.
    op.add_column("profile", sa.Column("pin_arrows", sa.Integer()))


def downgrade() -> None:
    with op.batch_alter_table("profile") as batch:
        batch.drop_column("pin_arrows")
