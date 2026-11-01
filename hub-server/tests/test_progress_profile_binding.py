from unittest.mock import AsyncMock


async def test_progress_profile_binding_rejects_before_history_or_provider(api, monkeypatch):
    policy = AsyncMock()
    report = AsyncMock()
    monkeypatch.setattr(api.c.content_policy, "require", policy)
    monkeypatch.setattr(api.c.progress, "report", report)
    payload = {"id": "tmdb:movie:123", "position_ms": 1000, "duration_ms": 2000, "state": "playing"}
    denied = await api.post("/playback/progress", json={**payload, "expected_profile_id": "previous-profile"})
    assert denied.status_code == 403 and denied.json()["code"] == "profile_changed"
    policy.assert_not_awaited()
    report.assert_not_awaited()
    # Explicit correct binding and legacy clients both remain compatible.
    for extra in ({"expected_profile_id": api.profile_id}, {}):
        accepted = await api.post("/playback/progress", json={**payload, **extra})
        assert accepted.status_code == 204, accepted.text
    assert report.await_count == 2
    assert all(call.args[0] == api.profile_id for call in report.await_args_list)
