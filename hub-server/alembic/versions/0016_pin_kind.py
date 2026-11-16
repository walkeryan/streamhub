"""Which kind of secret a profile's PIN is: a number PIN, an arrow pattern or a password."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profile", sa.Column("pin_kind", sa.String(16)))
    # Every secret set before this was digits: an arrow pattern when its length was recorded, else a typed PIN.
    op.execute("UPDATE profile SET pin_kind = 'arrows' WHERE pin_hash IS NOT NULL AND pin_arrows IS NOT NULL")
    op.execute("UPDATE profile SET pin_kind = 'pin' WHERE pin_hash IS NOT NULL AND pin_arrows IS NULL")


def downgrade() -> None:
    # A password stays in pin_hash and still checks, but an older hub's clients ask for it on a keypad,
    # which cannot type it: its owner signs in another way and chooses a PIN again.
    with op.batch_alter_table("profile") as batch:
        batch.drop_column("pin_kind")
