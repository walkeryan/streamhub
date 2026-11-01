from pathlib import Path

import httpx
import pytest

from tests.real_http_harness import fixture_app, outbound


async def test_real_http_fixture_boots_and_serves_actual_contract(tmp_path: Path):
    app = fixture_app(tmp_path, tmp_path / "dist", "http://127.0.0.1:5187")
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:5187") as client,
    ):
        login = await client.post("/api/v1/auth/login", json={"profile_id": "fixture-english"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        search = await client.get("/api/v1/search", params={"q": "Star Wars"}, headers=headers)
        assert search.status_code == 200, search.text
        results = search.json()["results"]
        assert results[0]["title"] == "Star Wars"
        assert any(row["id"] == "tmdb:movie:330459" and not row["sources"] for row in results)
        options = await client.get("/api/v1/playback/options", params={"id": "tmdb:movie:140607"}, headers=headers)
        assert options.status_code == 200 and options.json()["selection_required"]
        assert len(options.json()["options"]) == 2
        sports = await client.get("/api/v1/sports", headers=headers)
        assert sports.status_code == 200 and "Fixture A vs Fixture B" in sports.text
        generation = (await client.get("/api/v1/playback/generation", headers=headers)).json()["generation"]
        req = {"id": "tmdb:movie:140607", "recovery_generation": generation}
        assert (await client.post("/api/v1/playback/resolve", json=req, headers=headers)).status_code == 409
        chosen = await client.post(
            "/api/v1/playback/resolve",
            json={**req, "variant_id": "lib:fixture-iptv:en", "allow_unverified_audio": True},
            headers=headers,
        )
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["url"].startswith("http://127.0.0.1:5187/")


def test_fixture_rejects_unknown_outbound_network():
    with pytest.raises(AssertionError, match="Unexpected outbound fixture request"):
        outbound(httpx.Request("GET", "https://production.invalid/private"))
