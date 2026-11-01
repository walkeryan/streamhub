from sqlalchemy import update

from app.infra.models import Profile, Progress, Source, Title
from app.services.recommendations import recommendations
from tests.conftest import Api


async def seed(api: Api, count=0):
    async with api.c.db.session() as s:
        s.add_all(
            [
                Source(id="s", type="xtream", name="Library", config_enc="fixture", enabled=True),
                Source(id="disabled", type="xtream", name="Disabled", config_enc="fixture", enabled=False),
                Profile(id="other", name="Other", languages=["eng"]),
            ]
        )
        await s.execute(update(Profile).where(Profile.id == api.profile_id).values(languages=["eng"]))
        rows = [
            ("watched", "Action", 1, "s", "English"),
            ("action", "Action", 2, "s", "English"),
            ("action-copy", "Action", 2, "s", "English"),
            ("drama", "Drama", 3, "s", "English"),
            ("french", "Action", 4, "s", "French"),
            ("hidden", "Action", 5, "disabled", "English"),
        ]
        for ext, genre, tmdb, source, group in rows:
            s.add(
                Title(
                    source_id=source, ext_id=ext, kind="vod", title=ext, tmdb_id=tmdb, genres=[genre], group_name=group
                )
            )
        s.add_all(
            Title(
                source_id="s",
                ext_id=f"bulk{i}",
                kind="vod",
                title=f"Bulk {i}",
                tmdb_id=1000 + i,
                genres=["Comedy"],
                group_name="English",
            )
            for i in range(count)
        )
        await s.commit()


async def get(api, profile=None):
    pid = profile or api.profile_id
    ctx = await api.c.catalog.tile_context(pid)
    return await recommendations(api.c.catalog, pid, ctx)


async def test_personalized_genres_isolated_available_deduped_and_unwatched(api: Api):
    await seed(api)
    async with api.c.db.session() as s:
        s.add_all(
            [
                Progress(profile_id=api.profile_id, ref_id="lib:s:watched", position_ms=180_000),
                Progress(profile_id="other", ref_id="tmdb:movie:3", completed=True),
            ]
        )
        await s.commit()
    tiles, label = await get(api)
    assert label == "Because you watched Action"
    assert [t["id"] for t in tiles] == ["tmdb:movie:2"]
    other, label = await get(api, "other")
    assert label == "Because you watched Drama"
    assert other == []


async def test_cold_start_large_catalog_bounded_and_honest(api: Api):
    await seed(api, count=3000)
    tiles, label = await get(api)
    assert len(tiles) == 20
    assert label == "Explore your library · Not personalized"
    async with api.c.db.session() as s:
        # An accidental few seconds must not create a personalized signal.
        s.add(Progress(profile_id=api.profile_id, ref_id="lib:s:watched", position_ms=1000))
        await s.commit()
    assert (await get(api))[1] == label


async def test_empty_catalog_and_kids_not_claimed_safe(api: Api):
    assert (await get(api))[0] == []
    await seed(api)
    async with api.c.db.session() as s:
        await s.execute(update(Profile).where(Profile.id == api.profile_id).values(kids=True))
        await s.commit()
    assert (await get(api))[0] == []


async def test_episode_history_uses_local_show_and_excludes_duplicates(api: Api):
    await seed(api)
    async with api.c.db.session() as s:
        s.add_all(
            [
                Title(
                    source_id="s",
                    ext_id="show",
                    kind="series",
                    title="Show",
                    tmdb_id=10,
                    genres=["Action"],
                    group_name="English",
                ),
                Title(
                    source_id="s",
                    ext_id="copy",
                    kind="series",
                    title="Show copy",
                    tmdb_id=10,
                    genres=["Action"],
                    group_name="English",
                ),
                Title(source_id="s", ext_id="season", kind="season", title="Season", parent_ext_id="show"),
                Title(source_id="s", ext_id="episode", kind="episode", title="Episode", parent_ext_id="season"),
                Progress(profile_id=api.profile_id, ref_id="lib:s:episode", position_ms=180_000),
            ]
        )
        await s.commit()
    tiles, label = await get(api)
    assert label == "Because you watched Action"
    assert {t["id"] for t in tiles} == {"tmdb:movie:1", "tmdb:movie:2"}


async def test_old_watched_canonical_copy_not_recommended_outside_seed_window(api: Api):
    from datetime import UTC, datetime, timedelta

    await seed(api)
    async with api.c.db.session() as s:
        s.add(
            Progress(
                profile_id=api.profile_id,
                ref_id="lib:s:action-copy",
                completed=True,
                updated_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
        s.add(Progress(profile_id=api.profile_id, ref_id="lib:s:watched", completed=True))
        # Recent unmapped library history pushes the old title outside the 50 seed bound.
        s.add_all(Progress(profile_id=api.profile_id, ref_id=f"lib:missing:{i}", completed=True) for i in range(49))
        await s.commit()
    tiles, _ = await get(api)
    assert "tmdb:movie:2" not in {t["id"] for t in tiles}


async def test_history_without_genres_is_not_claimed_personalized(api: Api):
    await seed(api)
    async with api.c.db.session() as s:
        await s.execute(update(Title).where(Title.ext_id == "watched").values(genres=[]))
        s.add(Progress(profile_id=api.profile_id, ref_id="lib:s:watched", completed=True))
        await s.commit()
    assert (await get(api))[1] == "Explore your library · Not personalized"


async def test_episode_tmdb_namespace_never_matches_unrelated_show(api: Api):
    await seed(api)
    async with api.c.db.session() as s:
        s.add_all(
            [
                Title(source_id="s", ext_id="ep", kind="episode", title="Episode", tmdb_id=42, genres=["Drama"]),
                Title(
                    source_id="s",
                    ext_id="show42",
                    kind="series",
                    title="Show",
                    tmdb_id=42,
                    genres=["Action"],
                    group_name="English",
                ),
                Progress(profile_id=api.profile_id, ref_id="lib:s:ep", completed=True),
            ]
        )
        await s.commit()
    # Episode history uses its own genre, and does not equate its ID with TV42.
    _tiles, label = await get(api)
    assert label == "Because you watched Drama"
    async with api.c.db.session() as s:
        await s.execute(update(Title).where(Title.ext_id == "show42").values(genres=["Drama"]))
        await s.commit()
    assert "tmdb:tv:42" in {t["id"] for t in (await get(api))[0]}
