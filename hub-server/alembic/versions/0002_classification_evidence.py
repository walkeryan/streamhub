"""Add classification evidence without changing any access policy."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("programme", sa.Column("age_evidence", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "age_classification",
        sa.Column("subject_key", sa.String(256), primary_key=True),
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("age_classification")
    op.drop_column("programme", "age_evidence")
