from unittest.mock import AsyncMock

from app.infra.result_cache import ResultCache
from app.services.search_match import search_tokens, token_match_rank
from tests.test_canonical_discovery import setup


def test_short_transposition_remains_anchored():
    assert token_match_rank(search_tokens("star wras"), "star wars") is not None
    assert token_match_rank(search_tokens("star wxrs"), "star wars") is not None
    assert token_match_rank(search_tokens("stxr wras"), "star wars") is None


async def test_partial_result_not_admitted_but_next_success_cached():
    cache = ResultCache()
    load = AsyncMock(side_effect=[{"metadata_incomplete": True}, {"metadata_incomplete": False}])

    def can_cache(value):
        return not value["metadata_incomplete"]

    assert (await cache.get("query", load, 60, cacheable=can_cache))["metadata_incomplete"]
    assert "query" not in cache.entries
    assert not (await cache.get("query", load, 60, cacheable=can_cache))["metadata_incomplete"]
    await cache.get("query", load, 60, cacheable=can_cache)
    assert load.await_count == 2


async def test_partial_metadata_response_recovers_without_waiting_for_ttl(api):
    fake = await setup(api)
    successful = fake.search.return_value
    fake.search.side_effect = [RuntimeError("fixture outage"), successful]
    first = await api.get("/search", params={"q": "Star Wars", "language": "all"})
    assert first.status_code == 200 and not first.json()["metadata_available"]
    recovered = await api.get("/search", params={"q": "Star Wars", "language": "all"})
    assert recovered.status_code == 200 and recovered.json()["metadata_available"]
    assert fake.search.await_count == 2
    await api.get("/search", params={"q": " STAR   WARS ", "language": "all"})
    assert fake.search.await_count == 2
