"""Where a room member's playback descriptor comes from."""

from __future__ import annotations

from typing import Any

from app.proxy.stream_proxy import StreamProxy
from app.services.auth import Principal
from app.services.errors import Conflict, Forbidden
from app.services.playback import PlaybackResolver
from app.services.rooms import Room, position_at


class RoomPlayback:
    def __init__(self, playback: PlaybackResolver, proxy: StreamProxy) -> None:
        self._playback = playback
        self._proxy = proxy

    async def descriptor_for(
        self,
        room: Room,
        principal: Principal,
        client: str,
        *,
        now_ms: int,
        prefer_hls: bool = False,
        recovery_generation: str | None = None,
        allow_unverified_audio: bool = False,
        https_site: bool = False,
    ) -> dict[str, Any]:
        """The descriptor this member plays the room's title with.

        Today that is the member's own resolution, exactly as ``POST /playback/resolve``
        would answer it, so every refusal (rating ceiling, adult, audio selection, unknown
        variant) reaches a member who may not watch the title unchanged. Sharing one
        upstream between the members of a room on a limited source replaces this body.
        """
        self._proxy.ensure_running()
        if client == "web":
            if recovery_generation is None:
                raise Forbidden(
                    "Reload Streamhub to update playback recovery support.", code="playback_reload_required"
                )
            if recovery_generation != self._proxy.recovery_epoch:
                raise Forbidden(
                    "This playback attempt was stopped. Start playback again manually.", code="playback_stopped"
                )
        generation = self._proxy.generation
        result = await self._playback.resolve(
            principal.profile_id,
            room.ref,
            variant_id=room.variant_id,
            allow_unverified_audio=allow_unverified_audio,
            client="web_hls" if client == "web" and prefer_hls else client,
            https_site=https_site,
        )
        self._proxy.ensure_running(generation)
        if result.get("mode") == "handoff":
            # Another app plays it; there is no player here for the room to steer.
            raise Conflict("This title opens in another app and cannot be watched in a room.", code="room_unsupported")
        result["buffer_available"] = False
        if not room.live:
            # The room's clock decides where a member starts, not their own history.
            result["resume_position_ms"] = position_at(room.state, now_ms)
        return result
