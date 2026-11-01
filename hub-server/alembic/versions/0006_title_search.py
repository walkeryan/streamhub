"""Add transactional FTS candidates after the existing policy/PIN migration chain."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE VIRTUAL TABLE title_search USING fts5(title, norm_title, content='title', content_rowid='id')")
    op.execute("INSERT INTO title_search(title_search) VALUES ('rebuild')")
    op.execute("""CREATE TRIGGER title_search_insert AFTER INSERT ON title BEGIN
      INSERT INTO title_search(rowid,title,norm_title) VALUES(new.id,new.title,new.norm_title); END""")
    op.execute("""CREATE TRIGGER title_search_delete AFTER DELETE ON title BEGIN
      INSERT INTO title_search(title_search,rowid,title,norm_title) VALUES('delete',old.id,old.title,old.norm_title);
      END""")
    op.execute("""CREATE TRIGGER title_search_update AFTER UPDATE OF title,norm_title ON title BEGIN
      INSERT INTO title_search(title_search,rowid,title,norm_title) VALUES('delete',old.id,old.title,old.norm_title);
      INSERT INTO title_search(rowid,title,norm_title) VALUES(new.id,new.title,new.norm_title); END""")


def downgrade() -> None:
    for name in ("insert", "delete", "update"):
        op.execute(f"DROP TRIGGER IF EXISTS title_search_{name}")
    op.execute("DROP TABLE IF EXISTS title_search")
