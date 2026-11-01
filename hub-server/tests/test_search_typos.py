from __future__ import annotations

import pytest
from sqlalchemy import insert

from app.infra.models import Source, Title
from app.services.ids import normalise_title
from app.services.search_match import search_tokens, token_match_rank
from tests.conftest import Api
from tests.test_large_catalog_filters import seed_catalog


@pytest.mark.parametrize(
    ("query", "title", "matches"),
    [
        ("Stewart fails to save the universe", "Stuart Fails to Save the Universe", True),
        ("fails universe", "Stuart Fails to Save the Universe", True),
        ("universe stuart", "Stuart Fails to Save the Universe", True),
        ("stuart univ", "Stuart Fails to Save the Universe", True),
        ("stewart", "Stuart Fails to Save the Universe", False),
        ("stewart fials universse", "Stuart Fails to Save the Universe", False),
        ("stewart universe", "Stewart Little", False),
        ("star war", "Star Wars", True),
    ],
)
def test_bounded_token_matching(query, title, matches):
    tokens = search_tokens(normalise_title(query))
    assert (bool(tokens) and token_match_rank(tokens, normalise_title(title)) is not None) == matches


async def test_typo_partial_ranking_and_scope_on_large_catalog(api: Api):
    await seed_catalog(api, title_count=12000)
    canonical = "Stuart Fails to Save the Universe"
    async with api.c.db.session() as session:
        session.add(Source(id="other", name="Other", type="xtream", config_enc="unused"))
        await session.flush()
        await session.execute(
            insert(Title),
            [
                dict(
                    source_id=source,
                    ext_id=ext,
                    kind="series",
                    title=title,
                    norm_title=normalise_title(title),
                    group_name=group,
                )
                for source, ext, title, group in [
                    ("catalog", "canonical", canonical, "UK| Series"),
                    ("catalog", "exact", "Stewart Fails to Save the Universe", "UK| Series"),
                    ("catalog", "french", canonical, "FR| Series"),
                    ("catalog", "unknown", canonical, "HBO MAX SERIES"),
                    ("other", "other-match", canonical, "UK| Series"),
                    ("disabled", "disabled-match", canonical, "UK| Series"),
                ]
            ],
        )
        # Large single-anchor noise must rank below the full multiword candidate.
        await session.execute(
            insert(Title),
            [
                dict(
                    source_id="catalog",
                    ext_id=f"noise-{i}",
                    kind="series",
                    title=f"Universe Documentary {i}",
                    norm_title=f"universe documentary {i}",
                    group_name="UK| Series",
                )
                for i in range(600)
            ],
        )
        await session.commit()
    response = await api.get(
        "/search", params={"q": "Stewart fails to save the universe", "language": "eng", "source_id": "catalog"}
    )
    assert response.status_code == 200
    hits = response.json()["play_here"]
    assert [x["id"] for x in hits] == ["lib:catalog:exact", "lib:catalog:canonical"]
    partial = (
        await api.get("/search", params={"q": "  FAILS — UNIVERSE  ", "language": "eng", "source_id": "catalog"})
    ).json()["play_here"]
    assert {x["id"] for x in partial} == {"lib:catalog:exact", "lib:catalog:canonical"}
    unknown = (await api.get("/search", params={"q": "stewart fails universe", "language": "und"})).json()["play_here"]
    assert [x["id"] for x in unknown] == ["lib:catalog:unknown"]
    all_languages = (
        await api.get("/search", params={"q": "Stewart fails to save the universe", "language": "all"})
    ).json()["play_here"]
    assert {x["id"] for x in all_languages} == {
        "lib:catalog:canonical",
        "lib:catalog:exact",
        "lib:catalog:french",
        "lib:catalog:unknown",
        "lib:other:other-match",
    }
    isolated = (
        await api.get("/search", params={"q": "stewart fails universe", "language": "all", "source_id": "other"})
    ).json()["play_here"]
    assert [x["id"] for x in isolated] == ["lib:other:other-match"]

    other = (
        await api.get("/search", params={"q": "stewart fails universe", "language": "all", "source_id": "disabled"})
    ).json()
    assert other["play_here"] == []
    assert not (await api.get("/search", params={"q": "nonexistent fails universe", "language": "all"})).json()[
        "play_here"
    ]
