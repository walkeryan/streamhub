"""Private scene bookmarks and per-edition viewing cursors."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def profile() -> sa.Column:
    return sa.Column("profile_id", sa.String(64), sa.ForeignKey("profile.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "edition_progress",
        profile(),
        sa.Column("ref_id", sa.String(256), nullable=False),
        sa.Column("edition_id", sa.String(128), nullable=False),
        sa.Column("variant_id", sa.String(256), nullable=False),
        sa.Column("position_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("profile_id", "ref_id", "edition_id"),
    )
    op.create_table(
        "viewing_cursor",
        profile(),
        sa.Column("ref_id", sa.String(256), nullable=False),
        sa.Column("edition_id", sa.String(128), nullable=False),
        sa.Column("variant_id", sa.String(256), nullable=False),
        sa.Column("token", sa.String(40), nullable=False, unique=True),
        sa.PrimaryKeyConstraint("profile_id", "ref_id"),
    )
    op.create_table(
        "offline_copy",
        sa.Column("id", sa.String(32), primary_key=True),
        profile(),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("ref_id", sa.String(256), nullable=False),
        sa.Column("edition_id", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("baseline", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_offline_copy_profile_id", "offline_copy", ["profile_id"])
    op.create_table(
        "scene_bookmark",
        sa.Column("id", sa.String(32), primary_key=True),
        profile(),
        sa.Column("ref_id", sa.String(256), nullable=False),
        sa.Column("edition_id", sa.String(128), nullable=False),
        sa.Column("variant_id", sa.String(256), nullable=False),
        sa.Column("position_ms", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("preview", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scene_bookmark_profile_id", "scene_bookmark", ["profile_id"])

    reconcile_old_history()


def reconcile_old_history() -> None:
    """Each old direct movie carries an exact source. Promote its history and keep that timeline."""
    import hashlib
    import json
    import re

    connection = op.get_bind()
    rows = (
        connection.execute(
            sa.text("""
        SELECT p.id, p.profile_id, p.ref_id, p.position_ms, p.duration_ms, p.completed, p.updated_at,
               t.tmdb_id, t.title, t.extra, t.duration_ms AS catalog_duration
        FROM progress p JOIN title t ON p.ref_id = 'lib:' || t.source_id || ':' || t.ext_id
        WHERE t.tmdb_id IS NOT NULL AND t.kind IN ('movie','vod')
        ORDER BY p.updated_at, p.id
    """)
        )
        .mappings()
        .all()
    )
    for row in rows:
        ref = f"tmdb:movie:{row['tmdb_id']}"
        extra = json.loads(row["extra"]) if isinstance(row["extra"], str) else row["extra"]
        label = str((extra or {}).get("edition_title") or "").strip()
        if not label:
            match = re.search(
                r"\{edition-([^{}]{1,100})\}|\b(director['’]?s cut|theatrical cut|extended (?:cut|edition)|final cut|unrated (?:cut|edition))\b",
                row["title"],
                re.I,
            )
            label = next((value for value in match.groups() if value), "") if match else ""
        duration = row["catalog_duration"] or row["duration_ms"]
        edition = (
            "named:" + hashlib.sha256(label[:100].casefold().encode()).hexdigest()[:24]
            if label
            else f"runtime:{duration}"
            if duration
            else "variant:" + hashlib.sha256(row["ref_id"][4:].encode()).hexdigest()[:24]
        )
        values = dict(row)
        values.update(ref=ref, edition=edition)
        connection.execute(
            sa.text("""
            INSERT INTO edition_progress(profile_id,ref_id,edition_id,variant_id,position_ms,duration_ms,completed)
            VALUES(:profile_id,:ref,:edition,:ref_id,:position_ms,:duration_ms,:completed)
            ON CONFLICT(profile_id,ref_id,edition_id) DO UPDATE SET variant_id=excluded.variant_id,
              position_ms=excluded.position_ms,duration_ms=excluded.duration_ms,completed=excluded.completed
        """),
            values,
        )
        current = (
            connection.execute(
                sa.text("SELECT id,updated_at FROM progress WHERE profile_id=:profile_id AND ref_id=:ref"), values
            )
            .mappings()
            .first()
        )
        if current and current["updated_at"] >= row["updated_at"]:
            connection.execute(sa.text("DELETE FROM progress WHERE id=:id"), values)
        else:
            if current:
                connection.execute(sa.text("DELETE FROM progress WHERE id=:id"), {"id": current["id"]})
            connection.execute(sa.text("UPDATE progress SET ref_id=:ref WHERE id=:id"), values)


def downgrade() -> None:
    op.drop_table("offline_copy")
    op.drop_table("scene_bookmark")
    op.drop_table("viewing_cursor")
    op.drop_table("edition_progress")
