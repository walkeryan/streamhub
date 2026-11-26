"""Index canonical movie/series release dates and repair legacy Xtream title guesses."""

import re
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0018a"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "catalog_release",
        sa.Column("media_type", sa.String(8), primary_key=True),
        sa.Column("tmdb_id", sa.Integer(), primary_key=True),
        sa.Column("release_date", sa.String(10), nullable=False),
    )
    db = op.get_bind()
    # Same numeric id can name unrelated films and shows. Only exact detail keys whose
    # payload id matches the requested resource are authoritative. Never load credits/art.
    details = db.exec_driver_sql("""
        SELECT substr(key,1,instr(key,'?')-1), json_extract(value,'$.id'),
               CASE WHEN key LIKE 'tmdb:/movie/%' THEN json_extract(value,'$.release_date')
                    ELSE json_extract(value,'$.first_air_date') END
          FROM cache WHERE (key LIKE 'tmdb:/movie/%?%' OR key LIKE 'tmdb:/tv/%?%')
           AND substr(key,1,instr(key,'?')-1) NOT LIKE 'tmdb:/%/%/%'
         ORDER BY expires_at
    """)
    insert = sa.dialects.sqlite.insert(table)
    query = insert.on_conflict_do_update(
        index_elements=["media_type", "tmdb_id"], set_={"release_date": insert.excluded.release_date}
    )
    for batch in details.partitions(500):
        dates = []
        for path, tid, raw in batch:
            match = re.fullmatch(r"tmdb:/(movie|tv)/([1-9][0-9]*)", path)
            if not match or not isinstance(tid, int) or tid != int(match[2]) or not isinstance(raw, str):
                continue
            try:
                day = date.fromisoformat(raw).isoformat()
            except ValueError:
                continue
            if day == raw:
                dates.append({"media_type": match[1], "tmdb_id": tid, "release_date": day})
        if dates:
            db.execute(query, dates)

    # The old panel adapter took the first 19xx/20xx anywhere in the title when
    # metadata and an explicit trailing year were absent. Most rows need no repair.
    candidates = db.exec_driver_sql("""
        SELECT t.id,t.year,t.title,json_extract(t.extra,'$.raw_title'),
               json_extract(t.extra,'$.originally_available_at'),r.release_date
          FROM title t JOIN source s ON t.source_id=s.id
          LEFT JOIN catalog_release r ON r.tmdb_id=t.tmdb_id AND
               r.media_type=CASE t.kind WHEN 'vod' THEN 'movie' ELSE 'tv' END
         WHERE s.type='xtream' AND t.kind IN ('vod','series') AND t.year IS NOT NULL
           AND instr(t.title,CAST(t.year AS TEXT))>0
    """).all()
    updates = []
    for pk, year, title, raw_name, source_date, canonical in candidates:
        raw_name = raw_name or title
        match = re.search(r"(?:19|20)[0-9]{2}", raw_name)
        if not match or int(match[0]) != year:
            continue
        suffix = re.search(r"\(([12][0-9]{3})\)\s*(?:\([A-Z]{2}\))?\s*$", raw_name)
        if canonical:
            replacement = int(canonical[:4])
        elif isinstance(source_date, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", source_date):
            try:
                replacement = date.fromisoformat(source_date).year
            except ValueError:
                replacement = None
        elif suffix:
            replacement = int(suffix[1])
        else:
            replacement = None
        if replacement != year:
            updates.append((replacement, pk))
    if updates:
        db.exec_driver_sql("UPDATE title SET year=? WHERE id=?", updates)


def downgrade() -> None:
    op.drop_table("catalog_release")
