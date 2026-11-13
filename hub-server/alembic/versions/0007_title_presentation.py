"""Store presentation facts (language, quality, adult) on catalog rows."""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("title", sa.Column("language", sa.String(8), nullable=False, server_default="und"))
    op.add_column("title", sa.Column("quality", sa.String(16), nullable=True))
    op.add_column("title", sa.Column("adult", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Language filters read the column from now on. Carry over what the category
    # implied so existing catalogs keep their filters until the next sync rewrites it.
    from app.adapters.language.inferrer import lang_from_category

    bind = op.get_bind()
    groups = bind.execute(sa.text("SELECT DISTINCT group_name FROM title WHERE group_name IS NOT NULL")).scalars()
    by_language: dict[str, list[str]] = {}
    for group in groups:
        language = lang_from_category(group)
        if language:
            by_language.setdefault(language, []).append(group)
    update = sa.text("UPDATE title SET language = :language WHERE group_name IN :groups").bindparams(
        sa.bindparam("groups", expanding=True)
    )
    for language, names in by_language.items():
        for start in range(0, len(names), 500):
            bind.execute(update, {"language": language, "groups": names[start : start + 500]})
    op.create_index("ix_title_language", "title", ["language"])
    op.create_index("ix_title_adult", "title", ["adult"])


def downgrade() -> None:
    op.drop_index("ix_title_adult", table_name="title")
    op.drop_index("ix_title_language", table_name="title")
    # Plain DROP COLUMN, not a batch rebuild: recreating the table would drop the FTS triggers.
    for name in ("adult", "quality", "language"):
        op.drop_column("title", name)
