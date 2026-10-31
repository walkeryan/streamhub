from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.services.home import HomeService


async def test_continue_watching_never_imports_shared_source_history() -> None:
    progress = SimpleNamespace(in_progress=AsyncMock(return_value=[SimpleNamespace(ref_id="lib:s:own")]))
    own = {"id": "lib:s:own"}
    catalog = SimpleNamespace(tiles_for_ids=AsyncMock(return_value=[own]))
    sources = Mock()
    service = HomeService(catalog, Mock(), progress, Mock(), sources, Mock(), Mock())
    ctx = SimpleNamespace(sources={"s": SimpleNamespace(enabled=True, type="plex")})
    assert await service.continue_watching("profile-b", ctx) == [own]
    progress.in_progress.assert_awaited_once_with("profile-b", 30)
    catalog.tiles_for_ids.assert_awaited_once_with(["lib:s:own"], ctx)
    sources.provider.assert_not_called()
