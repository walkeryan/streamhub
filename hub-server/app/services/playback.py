"""PlaybackResolver: id → PlaybackDescriptor (direct | proxy | hls | handoff).

Mode decision for in-app sources (``decide_mode``):
- Explicit source/global policies retain their existing precedence.
- In auto mode, live MPEG-TS is codec-copy remuxed to HLS for stable client timelines.
  Direct progressive TS can freeze hardware video while audio advances on real IPTV feeds.
- VOD and existing adaptive streams retain their existing direct/proxy routing.
- A web client cannot decode most VOD files (MKV, HEVC, AC-3). Its files are probed once
  (``media_probe``) and anything a browser cannot play is served as HLS: H.264 video is copied,
  other video transcoded, and the chosen audio track encoded to AAC (``web_vod_plan``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, quote_plus

from app.adapters.language.inferrer import normalise_code
from app.domain.entities import (
    LIBRARY_SOURCE_TYPES,
    PlaybackMode,
    ProgrammeInfo,
    SourceType,
    StreamTarget,
)
from app.domain.ports import DeeplinkProvider, LiveProvider, MetadataProvider, PlaybackProvider
from app.infra.crypto import Crypto
from app.infra.models import Channel, Profile, Source, Title
from app.services import ids
from app.services.audio_selection import (
    audio_info,
    default_track,
    has_provider_tracks,
    preferred_audio,
    preferred_track,
    probe_tracks,
)
from app.services.availability import AvailabilityService
from app.services.catalog import CatalogService
from app.services.channels import ChannelService
from app.services.context import absolute
from app.services.errors import BadGateway, Conflict, Forbidden, NotFound, ServiceError
from app.services.media_probe import cached_probe
from app.services.rewind import archive_request, archive_window
from app.services.settings_service import SettingsService
from app.services.sources import SourceService
from app.services.streams import requested_ref
from app.services.tiles import row_to_channel, row_to_item
from app.services.user_state import ProgressService

log = logging.getLogger(__name__)

# A failed probe is not repeated on every request: each attempt holds an upstream connection.
PROBE_RETRY_S = 60.0
# One /playback/options call probes at most this many variants of a title.
MAX_OPTION_PROBES = 3
# Restart-at-offset sessions start on this grid, so a retry of the same position shares a session.
VOD_START_STEP_S = 5


def play_key(source_id: str, url: str, profile_id: str, session_id: str | None) -> str:
    """One viewer's playback of one file: its probe, its HLS sessions and its Range requests share this."""
    return f"{source_id}:{url}:{profile_id}:{session_id or ''}"


@dataclass(slots=True)
class StreamGrant:
    """Decoded contents of a /stream/{token}."""

    profile_id: str
    source_id: str
    url: str
    headers: dict[str, str]
    mode: str
    live: bool
    channel_key: str | None
    transcode: bool
    recovery_generation: int | None = None
    content_ref: str | None = None
    profile_version: int | None = None
    session_id: str | None = None
    policy_at: str | None = None
    policy_until: str | None = None
    pair_lease_id: str | None = None
    audio_variant: str | None = None
    audio_consent: bool = False
    audio_preferred: list[str] | None = None
    # Web VOD served as HLS (see ``web_vod_plan``): "copy" or "transcode" for the video,
    # the input codec and width for the encoder, the audio track and the start offset.
    vod_video: str | None = None
    vod_codec: str | None = None
    vod_width: int | None = None
    audio_track: int | None = None
    start_s: int = 0
    # What is playing, for the profile a busy source turns away (absent for adult titles).
    title: str | None = None
    kind: str | None = None
    # The user behind the profile and the version of their role and limits the grant was issued
    # under: every request made with the grant checks both again (``GrantGuard``).
    user_id: str | None = None
    user_version: int | None = None
    client: str | None = None
    # A watch room's member on a source that limits connections: the stream is the room's one
    # session (``RoomPlayback``), whatever the fields above say about start and audio.
    room: str | None = None


@dataclass(slots=True)
class WebVod:
    """What ``_resolve_title`` learned about one file for a web client."""

    video: str | None  # None: the browser decodes the file itself
    codec: str | None
    width: int | None
    tracks: list[dict[str, Any]]
    audio_track: int | None
    start_s: int
    duration_ms: int | None


@dataclass(slots=True)
class RoomScope:
    """A watch room resolving its title for one member (see ``app.services.room_playback``).

    A file from a source that limits connections, or that is always proxied, is played to the
    whole room from one upstream. The resolver then plans that session instead of the member's
    own, starting at ``position_ms``, and reports here what it found.
    """

    code: str
    position_ms: int
    shared: bool = False  # the resolver's answer: this title plays from the room's one session
    source_id: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    max_connections: int | None = None
    variant_id: str = ""  # the row that plays
    vod: WebVod | None = None
    title: str | None = None  # absent for adult titles
    kind: str | None = None


def vod_start_s(position_ms: int | None, duration_ms: int | None) -> int:
    """Where a restart-at-offset session starts for a position: on the ``VOD_START_STEP_S`` grid."""
    start_s = max(0, int(position_ms or 0)) // 1000 // VOD_START_STEP_S * VOD_START_STEP_S
    return 0 if duration_ms and start_s * 1000 >= duration_ms else start_s


class StreamTokens:
    def __init__(self, crypto: Crypto, ttl_s: int) -> None:
        self._crypto = crypto
        self.ttl_s = ttl_s
        self._generation = secrets.token_hex(16)

    def revoke_all(self) -> None:
        self._generation = secrets.token_hex(16)

    def issue(self, grant: StreamGrant) -> str:
        return self._crypto.encrypt_json(
            {
                "p": grant.profile_id,
                "ref": grant.content_ref,
                "ver": grant.profile_version,
                "sid": grant.session_id,
                "at": grant.policy_at,
                "until": grant.policy_until,
                "pair": grant.pair_lease_id,
                "av": grant.audio_variant,
                "ac": grant.audio_consent,
                "ap": grant.audio_preferred,
                "s": grant.source_id,
                "u": grant.url,
                "h": grant.headers,
                "m": grant.mode,
                "l": grant.live,
                "c": grant.channel_key,
                "t": grant.transcode,
                "vv": grant.vod_video,
                "vc": grant.vod_codec,
                "vw": grant.vod_width,
                "atr": grant.audio_track,
                "ss": grant.start_s,
                "ti": grant.title,
                "k": grant.kind,
                "uid": grant.user_id,
                "uv": grant.user_version,
                "cl": grant.client,
                "rm": grant.room,
                "iat": int(time.time()),
                "generation": self._generation,
            }
        )

    def verify(self, token: str) -> StreamGrant | None:
        d = self._crypto.try_decrypt_json(token, ttl=self.ttl_s)
        if not isinstance(d, dict) or d.get("generation") != self._generation:
            return None
        return StreamGrant(
            profile_id=str(d["p"]),
            content_ref=d.get("ref"),
            profile_version=d.get("ver"),
            session_id=d.get("sid"),
            policy_at=d.get("at"),
            policy_until=d.get("until"),
            pair_lease_id=d.get("pair"),
            audio_variant=d.get("av"),
            audio_consent=bool(d.get("ac")),
            audio_preferred=d.get("ap"),
            source_id=str(d["s"]),
            url=str(d["u"]),
            headers=dict(d.get("h") or {}),
            mode=str(d.get("m") or "proxy"),
            live=bool(d.get("l")),
            channel_key=d.get("c"),
            transcode=bool(d.get("t")),
            vod_video=d.get("vv"),
            vod_codec=d.get("vc"),
            vod_width=d.get("vw"),
            audio_track=d.get("atr"),
            start_s=int(d.get("ss") or 0),
            title=d.get("ti"),
            kind=d.get("k"),
            user_id=d.get("uid"),
            user_version=d.get("uv"),
            client=d.get("cl"),
            room=d.get("rm"),
        )


class ActiveStreams:
    """Port-free counter the proxy updates; lets the resolver see concurrent use per source."""

    def __init__(self) -> None:
        self._by_source: dict[str, int] = {}

    def inc(self, source_id: str) -> None:
        self._by_source[source_id] = self._by_source.get(source_id, 0) + 1

    def dec(self, source_id: str) -> None:
        n = self._by_source.get(source_id, 0) - 1
        if n <= 0:
            self._by_source.pop(source_id, None)
        else:
            self._by_source[source_id] = n

    def count(self, source_id: str) -> int:
        return self._by_source.get(source_id, 0)


def decide_mode(
    *,
    client: str,
    url: str,
    mime: str | None,
    live: bool,
    headers: dict[str, str],
    source_policy: str,
    global_mode: str,
    https_site: bool,
    max_connections: int | None,
    active_on_source: int,
    cors_ok: bool = True,
) -> PlaybackMode:
    """Pure decision function (see module docstring)."""
    policy = (source_policy or "auto").lower()
    gmode = (global_mode or "auto").lower()
    ts_live = live and (mime or "").lower() == "video/mp2t"
    if client == "web_hls" and ts_live:
        if policy == "never" or gmode == "never":
            return PlaybackMode.DIRECT
        return PlaybackMode.HLS
    if policy == "hls":
        return PlaybackMode.HLS
    if policy == "always" or gmode == "always":
        return PlaybackMode.PROXY
    if policy == "never" or gmode == "never":
        return PlaybackMode.DIRECT
    # Normalize live transport timestamps without re-encoding. Keep VOD unchanged.
    if ts_live:
        return PlaybackMode.HLS
    # auto
    if max_connections == 1 and active_on_source >= 1:
        return PlaybackMode.PROXY  # one upstream, fanned out by the shared proxy
    if client not in ("web", "web_hls"):
        return PlaybackMode.DIRECT
    if not cors_ok:
        # browser can't fetch it directly; raw MPEG-TS is remuxed so hls.js can play it
        return PlaybackMode.HLS if ts_live else PlaybackMode.PROXY
    if https_site and url.startswith("http://"):
        return PlaybackMode.PROXY
    if any(k.lower() in ("user-agent", "referer", "origin", "cookie") for k in headers):
        return PlaybackMode.PROXY
    return PlaybackMode.DIRECT


BROWSER_CONTAINERS = {"mp4", "mov"}
BROWSER_AUDIO = {"aac", "mp3"}
BROWSER_PIXELS = {None, "yuv420p", "yuvj420p"}  # 8-bit 4:2:0; a missing value is not a reason to transcode


def web_vod_plan(probe: dict[str, Any] | None, audio_track: int | None) -> str | None:
    """How a browser gets this file: ``None`` as it is, else HLS with the video ``copy``/``transcode``.

    As it is means H.264 with AAC or MP3 in an MP4/MOV container, on the first audio track (a browser
    cannot switch tracks in a progressive file). Otherwise H.264 is copied and anything else, or a
    file that could not be probed, is transcoded. In HLS the audio is always encoded to AAC stereo.
    """
    if probe is None:
        return "transcode"
    raw_video = probe.get("video")
    video = raw_video if isinstance(raw_video, dict) else {}
    h264 = not video or (video.get("codec") == "h264" and video.get("pix_fmt") in BROWSER_PIXELS)
    if not h264:
        return "transcode"
    tracks = probe_tracks({"probe": probe})
    index = audio_track or 0
    codec = tracks[index]["codec"] if index < len(tracks) else None
    container = set(str(probe.get("container") or "").split(","))
    if container & BROWSER_CONTAINERS and index == 0 and (not tracks or codec in BROWSER_AUDIO):
        return None
    return "copy"


class PlaybackResolver:
    def __init__(
        self,
        *,
        sources: SourceService,
        catalog: CatalogService,
        channels: ChannelService,
        availability: AvailabilityService,
        progress: ProgressService,
        settings: SettingsService,
        deeplinks: DeeplinkProvider,
        meta: MetadataProvider,
        tokens: StreamTokens,
        active: ActiveStreams,
    ) -> None:
        self._sources = sources
        self._catalog = catalog
        self._channels = channels
        self._availability = availability
        self._progress = progress
        self._settings = settings
        self._deeplinks = deeplinks
        self._meta = meta
        self.tokens = tokens
        self.active = active
        self._probing: dict[str, asyncio.Future[dict[str, Any] | None]] = {}
        self._probe_retry: dict[str, float] = {}
        self._recording_probes: dict[str, tuple[tuple[str, int], dict[str, Any]]] = {}

    content_policy: Any = None
    auth: Any = None
    # ``MediaProbe`` and the proxy's admission gate for it; without them nothing is ever probed.
    probe: Any = None
    probe_slot: Any = None
    # ``StreamProxy.pin``: the probe opens the file as the viewer's playback will, so a source that
    # hands out per-viewer sessions sees one viewer and not two back to back.
    pin: Any = None
    # ``StreamRegistry``: who is streaming what, for the per-user stream limit.
    streams: Any = None
    # ``RecorderService``: finished recordings play as ``rec:<id>``.
    recorder: Any = None

    async def _require_slot(self) -> None:
        """409 ``user_stream_limit`` when the caller's other devices already use every stream they may play."""
        from app.services.content_policy import active_principal

        principal = active_principal.get()
        if self.streams is not None and principal is not None and principal.user_id:
            await self.streams.require_slot(principal.user_id, principal.session_id, principal.limits.max_streams)

    def _admit(
        self, principal: Any, profile_id: str, ref: str, title: str | None, kind: str | None, client: str
    ) -> None:
        if self.streams is not None and principal is not None and principal.user_id:
            self.streams.admit(
                principal.user_id,
                profile_id,
                principal.session_id,
                ref,
                title=title,
                kind=kind,
                client=client,
                limit=principal.limits.max_streams,
            )

    def _governed(self) -> bool:
        """Does the content policy apply to whoever is asking? Under the legacy flag, to everyone;
        otherwise to a caller their own rating limits restrict."""
        from app.services.content_policy import active_principal

        return self.content_policy is not None and self.content_policy.applies_to(active_principal.get())

    async def _require_content(self, profile_id: str, ref: str, at: datetime | None = None) -> None:
        if self._governed():
            await self.content_policy.require(profile_id, ref, at=at)

    async def resolve(
        self,
        profile_id: str,
        hub_id: str,
        *,
        source_id: str | None = None,
        variant_id: str | None = None,
        allow_unverified_audio: bool = False,
        client: str = "web",
        catchup_programme_id: str | None = None,
        rewind_at: datetime | None = None,
        https_site: bool = False,
        audio_track: int | None = None,
        start_position_ms: int | None = None,
        room: RoomScope | None = None,
    ) -> dict[str, Any]:
        if rewind_at is not None and (catchup_programme_id or not hub_id.startswith("ch:")):
            raise ServiceError("Rewind requires a channel and cannot be combined with programme catch-up.")
        requested_ref.set(catchup_programme_id or hub_id)
        policy_at = rewind_at
        if catchup_programme_id:
            await self._require_content(profile_id, catchup_programme_id)
            policy_at = (await self._channels.programme(catchup_programme_id)).start
        await self._require_content(profile_id, hub_id.removeprefix("trailer:"), at=policy_at)
        if (
            hub_id.startswith("trailer:")
            and self.content_policy is not None
            and self.content_policy.enabled
            and self.content_policy.is_restricted(await self.content_policy.profile(profile_id))
        ):
            raise Forbidden("External trailers require owner access", code="content_restricted")
        client = client if client in ("web", "web_hls", "android_tv") else "web"
        web = client != "android_tv"
        if hub_id.startswith("rec:"):
            return await self._resolve_recording(profile_id, hub_id, client, audio_track, start_position_ms)
        if not web and room is None:
            # The TV plays the file itself and switches tracks itself; the field is accepted, not used.
            audio_track = start_position_ms = None
        if hub_id.startswith("trailer:"):
            return await self._resolve_trailer(profile_id, hub_id)
        if hub_id.startswith("prog:") and not catchup_programme_id:
            catchup_programme_id = hub_id
            prog = await self._channels.programme(hub_id)
            chans = await self._channels_for_epg(prog.epg_channel, prog.feed)
            if not chans:
                raise NotFound("no channel for programme")
            hub_id = ids.channel_id(chans[0].source_id, chans[0].ext_id)
        try:
            h = ids.parse(hub_id)
        except ValueError as exc:
            raise NotFound("unknown id") from exc
        if h.scheme == "ch":
            if source_id and source_id != h.a:
                raise NotFound("channel does not belong to the selected source")
            await self._check_channel_audio(profile_id, hub_id, variant_id, allow_unverified_audio)
            return await self._resolve_channel(
                profile_id,
                hub_id,
                client,
                catchup_programme_id,
                https_site,
                rewind_at,
                bool(variant_id and allow_unverified_audio),
            )
        if h.scheme == "lib":
            row = await self._catalog.title_row(h.a, h.b)
            if row is None:
                raise NotFound("title not found")
            row = await self._select_audio(
                profile_id, [row], variant_id, allow_unverified_audio, source_id, audio_track=audio_track, probe=web
            )
            playback_id = ids.lib_id(row.source_id, row.ext_id) if row.kind == "episode" else hub_id
            return await self._resolve_title(
                profile_id,
                row,
                playback_id,
                client,
                https_site,
                audio_consent=bool(variant_id and allow_unverified_audio),
                audio_track=audio_track,
                start_position_ms=start_position_ms,
                room=room,
            )
        if h.scheme == "tmdb" and h.a in ("movie", "tv") and h.b.isdigit():
            return await self._resolve_tmdb(
                profile_id,
                h.a,
                int(h.b),
                hub_id,
                source_id,
                client,
                https_site,
                variant_id,
                allow_unverified_audio,
                audio_track=audio_track,
                start_position_ms=start_position_ms,
                room=room,
            )
        raise NotFound("unknown id")

    # ------------------------------------------------------------------ media probe
    def _probe_applies(self, src: Source, target: StreamTarget) -> bool:
        """A file this server may open and ffprobe can read; manifests describe themselves."""
        if self.probe is None or not self.probe.available or target.live:
            return False
        if "never" in (self._sources.proxy_policy(src).lower(), str(self._settings.get("proxy_mode") or "").lower()):
            return False  # the owner said this server does not open the source's streams
        path = target.url.split("?", 1)[0].lower()
        mime = (target.mime or "").lower()
        if path.endswith((".m3u8", ".mpd")) or "mpegurl" in mime or "dash" in mime:
            return False
        return target.url.startswith(("http://", "https://"))

    async def _probe_file(
        self, row: Title, src: Source, target: StreamTarget, profile_id: str, pin_key: str | None = None
    ) -> dict[str, Any] | None:
        """This row's probe, from the cache or by asking the file now. ``None``: could not be learned.

        Only ever called for the one title being opened: a probe holds an upstream connection for up
        to ten seconds, so it also has to be admitted like a stream on a connection-limited source.
        """
        cached = cached_probe(row.extra)
        if cached is not None:
            return cached
        key = ids.lib_id(row.source_id, row.ext_id)
        if self._probe_retry.get(key, 0.0) > time.monotonic():
            return None
        running = self._probing.get(key)
        if running is None:
            from app.services.content_policy import active_principal

            principal = active_principal.get()
            viewer = pin_key or play_key(
                src.id, target.url, profile_id, principal.session_id if principal is not None else None
            )
            running = asyncio.ensure_future(self._run_probe(key, row, src, target, viewer))
            self._probing[key] = running
            running.add_done_callback(lambda _: self._probing.pop(key, None))
        # Shielded: a second request for the same title may be waiting on this probe.
        found = await asyncio.shield(running)
        if found is not None:
            row.extra = {**(row.extra or {}), "probe": found}
        return found

    async def _run_probe(
        self, key: str, row: Title, src: Source, target: StreamTarget, viewer: str
    ) -> dict[str, Any] | None:
        from sqlalchemy import select

        found: dict[str, Any] | None = None
        try:
            async with contextlib.AsyncExitStack() as stack:
                if self.probe_slot is not None:
                    await stack.enter_async_context(self.probe_slot(src.id, self._sources.max_connections(src)))
                url = target.url
                if self.pin is not None:
                    url = await self.pin(viewer, url, dict(target.headers))
                found = await self.probe.run(url, dict(target.headers))
        except Conflict:
            return None  # every connection is in use; never take one from a viewer to probe
        except Exception as exc:
            log.warning("media probe failed", extra={"source": src.id, "err": type(exc).__name__})
        if found is None:
            self._probe_retry[key] = time.monotonic() + PROBE_RETRY_S
            return None
        async with self._sources._db.session() as s:
            stored = (
                await s.execute(select(Title).where(Title.source_id == row.source_id, Title.ext_id == row.ext_id))
            ).scalar_one_or_none()
            if stored is not None:
                stored.extra = {**(stored.extra or {}), "probe": found}
                await s.commit()
        return found

    async def _probe_row(self, row: Title, profile_id: str) -> None:
        """Probe a listed variant whose source reported no audio tracks (``/playback/options``)."""
        if self.probe is None or not self.probe.available or cached_probe(row.extra) is not None:
            return
        try:
            src, prov = await self._sources.provider_for(row.source_id)
            if not src.enabled or not isinstance(prov, PlaybackProvider):
                return
            target = await prov.stream_for_item(row_to_item(row))
        except Exception:
            return  # resolving the stream fails loudly at playback; options stay unverified
        if self._probe_applies(src, target):
            await self._probe_file(row, src, target, profile_id)

    async def _audio_options(self, profile_id: str, rows: list[Title], probe: bool = False) -> list[dict[str, Any]]:
        sources = await self._sources.rows_by_id()
        preferred = await self._catalog.preferred_languages(profile_id, None)
        options = []
        probes = MAX_OPTION_PROBES if probe else 0
        for row in sorted(
            rows, key=lambda r: (getattr(sources.get(r.source_id), "priority", 100), r.source_id, r.ext_id)
        ):
            src = sources.get(row.source_id)
            if src is None or not src.enabled:
                continue
            evidence_row = row
            if row.kind in ("show", "series", "season"):
                try:
                    evidence_row = await self._next_episode(profile_id, row)
                except NotFound:
                    continue
            extra = evidence_row.extra or {}
            if probes > 0 and not has_provider_tracks(extra) and cached_probe(extra) is None:
                probes -= 1
                await self._probe_row(evidence_row, profile_id)
                extra = evidence_row.extra or {}
            info = audio_info(extra, label=row.title, group=row.group_name, language=row.language)
            tracks = probe_tracks(extra)
            wanted = preferred_track(tracks, preferred)
            options.append(
                {
                    "variant_id": ids.lib_id(row.source_id, row.ext_id),
                    "source_id": row.source_id,
                    "source_name": src.name,
                    "provider_label": row.title,
                    "kind": row.kind,
                    "year": row.year,
                    **info,
                    "audio_tracks": tracks,
                    "preferred_audio_track": wanted,
                    # A probed file's tracks can each be selected, so any of them may satisfy the profile.
                    "preferred_audio_available": preferred_audio(info, preferred) or wanted is not None,
                }
            )
        return options

    async def _channel_audio_option(self, profile_id: str, hub_id: str) -> dict[str, Any]:
        ch = await self._authorized_channel(profile_id, hub_id)
        src = await self._sources.get(ch.source_id)
        preferred = await self._catalog.preferred_languages(profile_id, None)
        info = audio_info(ch.data or {}, label=ch.name, group=ch.group_name)
        # Even a provider language tag is only a hint unless actual audio-track
        # metadata was retained. Never promote inferred Channel.language to proof.
        info["language_hint"] = normalise_code(ch.language) or info["language_hint"]
        return {
            "variant_id": ids.channel_id(ch.source_id, ch.ext_id),
            "source_id": ch.source_id,
            "source_name": src.name,
            "provider_label": ch.name,
            "kind": "channel",
            "year": None,
            **info,
            "preferred_audio_available": preferred_audio(info, preferred),
        }

    async def _check_channel_audio(
        self, profile_id: str, hub_id: str, variant_id: str | None, allow_unverified_audio: bool
    ) -> None:
        option = await self._channel_audio_option(profile_id, hub_id)
        if variant_id is not None and variant_id != option["variant_id"]:
            raise NotFound("audio variant does not belong to this channel")
        preferred = await self._catalog.preferred_languages(profile_id, None)
        if preferred and not option["preferred_audio_available"] and not (variant_id and allow_unverified_audio):
            raise Conflict(
                "No verified preferred audio. Confirm this channel's audio source.", code="audio_selection_required"
            )

    async def options(self, profile_id: str, hub_id: str, source_id: str | None = None) -> dict[str, Any]:
        await self._require_content(profile_id, hub_id)
        try:
            h = ids.parse(hub_id)
        except ValueError as exc:
            raise NotFound("unknown id") from exc
        rows: list[Title] = []
        channel_id = hub_id if h.scheme == "ch" else None
        if h.scheme == "prog":
            prog = await self._channels.programme(hub_id)
            channels = await self._channels_for_epg(prog.epg_channel, prog.feed)
            if not channels:
                raise NotFound("no channel for programme")
            channel_id = ids.channel_id(channels[0].source_id, channels[0].ext_id)
        if channel_id:
            option = await self._channel_audio_option(profile_id, channel_id)
            if source_id and source_id != option["source_id"]:
                raise NotFound("channel does not belong to the selected source")
            preferred = await self._catalog.preferred_languages(profile_id, None)
            automatic = option["variant_id"] if not preferred or option["preferred_audio_available"] else None
            return {
                "id": hub_id,
                "preferred_languages": preferred,
                "options": [option],
                "automatic_variant_id": automatic,
                "selection_required": automatic is None,
            }
        if h.scheme == "lib":
            row = await self._catalog.title_row(h.a, h.b)
            if row is not None:
                rows = [row]
        elif h.scheme == "tmdb" and h.a in ("movie", "tv") and h.b.isdigit():
            sources = await self._sources.rows_by_id()
            meta = await self._meta.details(h.a, int(h.b)) if self._meta.enabled else None
            _, rows = await self._availability.badges_for(
                h.a,
                tmdb_id=int(h.b),
                imdb_id=meta.imdb_id if meta else None,
                title=meta.title if meta else None,
                year=meta.year if meta else None,
                sources=sources,
                is_in_app=self._sources.registry.is_in_app,
            )
        elif h.scheme != "ch":
            raise NotFound("unknown id")
        if source_id:
            rows = [row for row in rows if row.source_id == source_id]
        options = await self._audio_options(profile_id, rows, probe=True)
        preferred = await self._catalog.preferred_languages(profile_id, None)
        automatic = next((o["variant_id"] for o in options if not preferred or o["preferred_audio_available"]), None)
        return {
            "id": hub_id,
            "preferred_languages": preferred,
            "options": options,
            "automatic_variant_id": automatic,
            "selection_required": bool(options and not automatic),
        }

    async def _select_audio(
        self,
        profile_id: str,
        rows: list[Title],
        variant_id: str | None,
        allow_unverified_audio: bool,
        source_id: str | None = None,
        *,
        audio_track: int | None = None,
        probe: bool = False,
    ) -> Title:
        if source_id:
            rows = [row for row in rows if row.source_id == source_id]
        options = await self._audio_options(profile_id, rows, probe=probe)
        preferred = await self._catalog.preferred_languages(profile_id, None)
        # Naming a variant together with one of its tracks is as explicit as confirming the variant.
        explicit = bool(variant_id) and (allow_unverified_audio or audio_track is not None)
        if variant_id:
            selected = next((o for o in options if o["variant_id"] == variant_id), None)
            if selected is None:
                raise NotFound("variant does not belong to this title or source")
            if preferred and not selected["preferred_audio_available"] and not explicit:
                raise Conflict(
                    "Confirm this source's unverified or nonpreferred audio.", code="audio_selection_required"
                )
        else:
            selected = next((o for o in options if not preferred or o["preferred_audio_available"]), None)
            if selected is None:
                if not options:
                    raise NotFound("no playable variants for this title")
                raise Conflict(
                    "No verified preferred audio. Choose and confirm a source.", code="audio_selection_required"
                )
        row = next(r for r in rows if ids.lib_id(r.source_id, r.ext_id) == selected["variant_id"])
        if row.kind in ("show", "series", "season"):
            row = await self._next_episode(profile_id, row)
            # Progress may change while the chooser is open or during another await.
            # Recheck the actual episode, never inherit a previous episode's evidence.
            info = audio_info(row.extra or {}, label=row.title, group=row.group_name, language=row.language)
            wanted = preferred_track(probe_tracks(row.extra or {}), preferred)
            if preferred and not preferred_audio(info, preferred) and wanted is None and not explicit:
                raise Conflict("Confirm the next episode's audio source.", code="audio_selection_required")
        return row

    async def _resolve_trailer(self, profile_id: str, hub_id: str) -> dict[str, Any]:
        """``trailer:<title id>`` → hand-off to the YouTube app (specific video if TMDB knows one)."""
        title_id = hub_id.split(":", 1)[1]
        try:
            h = ids.parse(title_id)
        except ValueError as exc:
            raise NotFound("unknown id") from exc
        title: str | None = None
        year: int | None = None
        poster: str | None = None
        media_type: str | None = None
        tmdb_id: int | None = None
        if h.scheme == "tmdb" and h.b.isdigit():
            media_type, tmdb_id = h.a, int(h.b)
            meta = await self._meta.details(media_type, tmdb_id) if self._meta.enabled else None
            if meta:
                title, year, poster = meta.title, meta.year, meta.poster
        elif h.scheme == "lib":
            row = await self._catalog.title_row(h.a, h.b)
            if row is None:
                raise NotFound("title not found")
            title, year = row.title, row.year
            if row.tmdb_id:
                media_type, tmdb_id = ids.tmdb_type_for_kind(row.kind), row.tmdb_id
        else:
            raise NotFound("unknown id")
        if not title:
            raise NotFound("title not found")
        key = None
        if media_type and tmdb_id and self._meta.enabled:
            try:
                key = await self._meta.trailer_key(media_type, tmdb_id)
            except Exception:
                key = None
        if key:
            url = f"https://www.youtube.com/watch?v={quote(key, safe='')}"
        else:
            query = " ".join(x for x in (title, str(year) if year else "", "trailer") if x)
            url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        return {
            "id": hub_id,
            "mode": PlaybackMode.HANDOFF.value,
            "url": None,
            "mime": None,
            "headers": {},
            "live": False,
            "resume_position_ms": 0,
            "duration_ms": None,
            "title": title,
            "subtitle": "Trailer on YouTube",
            "poster": poster,
            "channel_context": None,
            "handoff": {
                "service_key": "youtube",
                "android_package": "com.google.android.youtube.tv",
                "android_uri": url,
                "android_component": None,
                "android_extras": {},
                "web_url": url,
                "supports_title_link": key is not None,
            },
        }

    async def _channels_for_epg(self, epg_channel: str, feed: str) -> list[Channel]:
        from sqlalchemy import select

        async with self._sources._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(Channel)
                        .join(Source, Source.id == Channel.source_id)
                        .where(
                            Channel.epg_id == epg_channel,
                            Channel.source_id == feed,
                            Source.enabled.is_(True),
                        )
                        .order_by(Channel.id)
                    )
                ).scalars()
            )

    async def rewind_capability(self, profile_id: str, hub_id: str) -> dict[str, Any]:
        ch = await self._authorized_channel(profile_id, hub_id)
        return archive_window(ch)

    async def _authorized_channel(self, profile_id: str, hub_id: str, at: datetime | None = None) -> Channel:
        async with self._sources._db.session() as session:
            if await session.get(Profile, profile_id) is None:
                raise NotFound("profile not found")
        await self._require_content(profile_id, hub_id, at=at)
        from app.services.content_policy import active_policy

        reset = active_policy.set(None)
        try:
            ch = await self._channels.channel_by_hub_id(hub_id)
        finally:
            active_policy.reset(reset)
        src = await self._sources.get(ch.source_id)
        if not src.enabled:
            raise NotFound("channel source is disabled")
        return ch

    # ------------------------------------------------------------------ channels
    async def _resolve_channel(
        self,
        profile_id: str,
        hub_id: str,
        client: str,
        catchup_programme_id: str | None,
        https_site: bool,
        rewind_at: datetime | None = None,
        audio_consent: bool = False,
    ) -> dict[str, Any]:
        policy_at = rewind_at
        if catchup_programme_id:
            await self._require_content(profile_id, catchup_programme_id)
            policy_at = (await self._channels.programme(catchup_programme_id)).start
        ch = await self._authorized_channel(profile_id, hub_id, at=policy_at)
        await self._require_slot()
        src, prov = await self._sources.provider_for(ch.source_id)
        if not isinstance(prov, LiveProvider):
            raise ServiceError("source cannot play live channels")
        info = row_to_channel(ch)
        target: StreamTarget | None
        title = ch.name
        subtitle = None
        archive_start = archive_end = None
        if rewind_at is not None:
            archive_start, archive_end = archive_request(ch, rewind_at)
            pinfo = ProgrammeInfo(
                channel=ch.epg_id or ch.ext_id, start=archive_start, stop=archive_end, title="Provider archive"
            )
            if self._governed():
                await self.content_policy.require_channel_interval(profile_id, hub_id, pinfo.start, pinfo.stop)
            target = await prov.catchup_for_channel(info, pinfo)
            if target is None:
                raise ServiceError("Provider did not supply the advertised archive.", code="rewind_unavailable")
            subtitle = "Provider archive · " + archive_start.isoformat()
        elif catchup_programme_id:
            await self._require_content(profile_id, catchup_programme_id)
            prog = await self._channels.programme(catchup_programme_id)
            now = datetime.now(UTC)
            if prog.feed != ch.source_id or prog.epg_channel != ch.epg_id:
                raise NotFound("programme does not belong to this channel")
            if ch.catchup_days <= 0 or prog.start < now - timedelta(days=ch.catchup_days) or prog.stop > now:
                raise ServiceError("Programme is outside provider archive retention.", code="catchup_unavailable")
            archive_start, archive_end = prog.start, prog.stop
            pinfo = ProgrammeInfo(channel=prog.epg_channel, start=prog.start, stop=prog.stop, title=prog.title)
            if self._governed():
                await self.content_policy.require_channel_interval(profile_id, hub_id, pinfo.start, pinfo.stop)
            target = await prov.catchup_for_channel(info, pinfo)
            if target is None:
                raise ServiceError("catch-up not available for this programme", code="catchup_unavailable")
            subtitle = prog.title
        else:
            target = await prov.stream_for_channel(info)
        # channel context: position within the profile's current default list
        ctx = None
        try:
            ctx = await self._channels.context_for_channel(profile_id, ch.id)
        except Exception:
            ctx = None
        desc = self._descriptor(
            profile_id,
            src,
            hub_id,
            target,
            client,
            https_site,
            channel_key=None if catchup_programme_id or rewind_at is not None else f"{src.id}:{ch.ext_id}",
            title=title,
            subtitle=subtitle,
            poster=self._catalog.image_fn({src.id: src})(src, ch.logo, ids.channel_id(ch.source_id, ch.ext_id)),
            policy_ref=hub_id,
            policy_at=archive_start,
            policy_until=archive_end,
            kind="channel",
        )
        desc.update(await self._runtime_audio(profile_id, ch.data or {}, audio_consent))
        desc["archive_start"] = archive_start
        desc["archive_end"] = archive_end
        desc["channel_context"] = ctx
        return desc

    # ------------------------------------------------------------------ titles
    async def _next_episode(self, profile_id: str, show: Title) -> Title:
        await self._progress.merge_account_state(profile_id, [show])
        kids = await self._catalog.children_rows(show)
        eps = [k for k in kids if k.kind == "episode"]
        if not eps:
            raise NotFound("no episodes")
        for e in eps:
            p = await self._progress.get(profile_id, ids.lib_id(e.source_id, e.ext_id))
            if p is None or not p.completed:
                return e
        return eps[0]

    async def room_episode(
        self, profile_id: str, hub_id: str, variant_id: str | None = None
    ) -> tuple[Title, Title] | None:
        """The (show, episode) a watch room made from ``hub_id`` is on, or None when that is not a show.

        The episode is the one ``resolve`` would start for this profile now: the next one of the
        variant named, or of the variant ``resolve`` would pick. It is asked once, for whoever
        makes the room, because members who each resolved the show would each get their own.
        Nothing is probed or opened, and nothing is refused over audio: each member's own
        resolution of the episode still answers for that.
        """
        try:
            h = ids.parse(hub_id)
        except ValueError:
            return None
        rows: list[Title] = []
        if h.scheme == "lib":
            row = await self._catalog.title_row(h.a, h.b)
            rows = [row] if row is not None else []
        elif h.scheme == "tmdb" and h.a == "tv" and h.b.isdigit():
            rows = (await self._tmdb_matches(h.a, int(h.b)))[3]
        rows = [r for r in rows if r.kind in ("show", "series", "season")]
        if not rows:
            return None
        options = await self._audio_options(profile_id, rows)
        if variant_id:
            chosen = next((o for o in options if o["variant_id"] == variant_id), None)
            if chosen is None:
                raise NotFound("variant does not belong to this title or source")
        elif options:
            preferred = await self._catalog.preferred_languages(profile_id, None)
            chosen = next((o for o in options if not preferred or o["preferred_audio_available"]), options[0])
        else:
            raise NotFound("no playable variants for this title")
        show = next(r for r in rows if ids.lib_id(r.source_id, r.ext_id) == chosen["variant_id"])
        return show, await self._next_episode(profile_id, show)

    async def _resolve_title(
        self,
        profile_id: str,
        row: Title,
        hub_id: str,
        client: str,
        https_site: bool,
        progress_ids: list[str] | None = None,
        audio_consent: bool = False,
        audio_track: int | None = None,
        start_position_ms: int | None = None,
        room: RoomScope | None = None,
    ) -> dict[str, Any]:
        await self._require_content(profile_id, ids.lib_id(row.source_id, row.ext_id))
        await self._require_slot()
        src, prov = await self._sources.provider_for(row.source_id)
        if not isinstance(prov, PlaybackProvider):
            raise ServiceError("source cannot play titles")
        try:
            target = await prov.stream_for_item(row_to_item(row))
        except LookupError as exc:
            raise NotFound(str(exc)) from exc
        except Exception as exc:
            raise BadGateway("could not resolve stream from source") from exc
        lib = ids.lib_id(row.source_id, row.ext_id)
        keys = [hub_id, lib, *(progress_ids or [])]
        # The one user linked to the library server's account resumes where that account is, if newer.
        await self._progress.merge_account_state(profile_id, [row])
        p = await self._progress.get(profile_id, *keys)
        # Provider resume belongs to the shared source account, not this profile.
        resume = 0
        if p is not None:
            resume = 0 if p.completed else p.position_ms
        target.resume_position_ms = resume
        subtitle = target.subtitle
        if row.kind == "episode":
            subtitle = f"S{row.season_number or 0:02d}E{row.episode_number or 0:02d}" + (
                f" · {target.subtitle}" if target.subtitle else ""
            )
        image = self._catalog.image_fn({src.id: src})
        vod = None
        if room is not None and self._room_shares(src, target):
            # One session for every member, whatever their client: planned as for a browser, opened
            # (and probed) as the room so the source sees a single viewer.
            vod = await self._web_vod(
                profile_id, row, src, target, audio_track, room.position_ms, pin_key=f"room:{room.code}"
            )
            room.shared = True
            room.source_id, room.url, room.headers = src.id, target.url, dict(target.headers)
            room.max_connections = self._sources.max_connections(src)
            room.variant_id, room.vod = lib, vod
            room.title, room.kind = None if row.adult else row.title, row.kind
        elif client != "android_tv" and self._probe_applies(src, target):
            vod = await self._web_vod(profile_id, row, src, target, audio_track, start_position_ms)
        desc = self._descriptor(
            profile_id,
            src,
            hub_id,
            target,
            client,
            https_site,
            channel_key=None,
            title=row.title,
            subtitle=subtitle,
            poster=image(src, row.poster, ids.lib_id(row.source_id, row.ext_id)),
            vod=vod,
            kind=row.kind,
            private=bool(row.adult),
            room=room.code if room is not None and room.shared else None,
        )

        desc.update(await self._runtime_audio(profile_id, row.extra or {}, audio_consent))
        if vod is not None and desc["audio_track"] is not None:
            # The server encodes exactly one track, so that track is the whole answer.
            track = vod.tracks[desc["audio_track"]]
            known = track["language"] != "und"
            desc.update(
                audio_languages=[track["language"]] if known else [],
                audio_verified=known,
                audio_evidence="media_probe" if known else "unknown",
                default_audio_language=track["language"] if known else None,
            )
            if audio_track is not None:
                desc["audio_language_required"] = False
        return desc

    def _room_shares(self, src: Source, target: StreamTarget) -> bool:
        """Does a watch room play this from one upstream? A file this server may open, from a source
        that limits connections or is always proxied. Other titles cost no budget: each member plays their own."""
        policy = self._sources.proxy_policy(src).lower()
        if target.live or "never" in (policy, str(self._settings.get("proxy_mode") or "").lower()):
            return False
        if not target.url.startswith(("http://", "https://")):
            return False
        return bool(self._sources.max_connections(src)) or policy == "always"

    async def _web_vod(
        self,
        profile_id: str,
        row: Title,
        src: Source,
        target: StreamTarget,
        audio_track: int | None,
        start_position_ms: int | None,
        pin_key: str | None = None,
    ) -> WebVod:
        """Probe the file and plan its delivery to a browser (see ``web_vod_plan``).

        Seeking in the HLS form is restart-at-offset: the session starts at ``start_s`` (the requested
        position, else the profile's resume position, floored to ``VOD_START_STEP_S``) and the client
        asks for a new start when the viewer seeks outside what that session has produced.

        ``pin_key`` is a watch room planning its one session: the probe uses the room's upstream
        session, a file that cannot be probed is still planned (transcoded), and so is one a browser
        would have played as it is (copied).
        """
        probe = None
        if self._probe_applies(src, target):
            probe = await self._probe_file(row, src, target, profile_id, pin_key)
        vod = await self._plan_vod(
            profile_id, probe, target.duration_ms, target.resume_position_ms, audio_track, start_position_ms
        )
        if pin_key is not None and vod.video is None:
            vod.video = "copy"  # a room's members share one HLS session even when a browser could play the file
        return vod

    async def _plan_vod(
        self,
        profile_id: str,
        probe: dict[str, Any] | None,
        duration_ms: int | None,
        resume_ms: int | None,
        audio_track: int | None,
        start_position_ms: int | None,
    ) -> WebVod:
        """A probed (or unprobeable) file as a browser gets it: audio track, video plan, start offset."""
        tracks = probe_tracks({"probe": probe}) if probe else []
        if audio_track is not None and probe is not None and audio_track >= len(tracks):
            raise ServiceError("This file has no such audio track.", code="audio_track_invalid")
        selected = audio_track if probe is not None else None
        if selected is None and tracks:
            preferred = await self._catalog.preferred_languages(profile_id, None)
            wanted = preferred_track(tracks, preferred)
            selected = wanted if wanted is not None else default_track(tracks)
        raw_video = probe.get("video") if probe else None
        video = raw_video if isinstance(raw_video, dict) else {}
        duration_ms = duration_ms or (probe.get("duration_ms") if probe else None)
        start_ms = resume_ms if start_position_ms is None else start_position_ms
        start_s = vod_start_s(start_ms, duration_ms)
        return WebVod(
            video=web_vod_plan(probe, selected),
            codec=video.get("codec"),
            width=video.get("width"),
            tracks=tracks,
            audio_track=selected,
            start_s=start_s,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------ recordings
    async def _resolve_recording(
        self, profile_id: str, hub_id: str, client: str, audio_track: int | None, start_position_ms: int | None
    ) -> dict[str, Any]:
        """``rec:<recording id>``: a recording's file, to anyone who may see the recording.

        The TV gets the file itself, with Range. A browser gets what it gets for any VOD file:
        the file is probed and, since it is Matroska, served as HLS with the video copied
        (H.264) or transcoded and one audio track encoded.
        """
        from app.services.content_policy import active_principal

        principal = active_principal.get()
        if self.recorder is None or principal is None:
            raise NotFound("recording not found")
        row, path = await self.recorder.playable(principal, hub_id.removeprefix("rec:"))
        await self._require_slot()
        p = await self._progress.get(profile_id, hub_id)
        resume = 0 if p is None or p.completed else p.position_ms
        vod: WebVod | None = None
        probe: dict[str, Any] | None = None
        if client != "android_tv":
            probe = await self._probe_recording(row.id, path)
            vod = await self._plan_vod(profile_id, probe, None, resume, audio_track, start_position_ms)
        plan = vod if vod is not None and vod.video is not None else None
        mode = PlaybackMode.HLS if plan is not None else PlaybackMode.PROXY
        device = client if client == "android_tv" else "web"
        title = row.title[:80]
        self._admit(principal, profile_id, hub_id, title, "recording", device)
        token = self.tokens.issue(
            StreamGrant(
                profile_id=profile_id,
                content_ref=hub_id,
                profile_version=principal.token_version,
                session_id=principal.session_id,
                source_id="",
                url=hub_id,  # the file is looked up when the grant is used, never named in it
                headers={},
                mode=mode.value,
                live=False,
                channel_key=None,
                transcode=False,
                vod_video=plan.video if plan else None,
                vod_codec=plan.codec if plan else None,
                vod_width=plan.width if plan else None,
                audio_track=plan.audio_track if plan else None,
                start_s=plan.start_s if plan else 0,
                title=title,
                kind="recording",
                user_id=principal.user_id or None,
                user_version=principal.user_version,
                client=device,
            )
        )
        hls = mode == PlaybackMode.HLS
        desc: dict[str, Any] = {
            "id": hub_id,
            "mode": mode.value,
            "url": absolute(f"/api/v1/stream/{token}/index.m3u8" if hls else f"/api/v1/stream/{token}"),
            "mime": "application/x-mpegURL" if hls else "video/x-matroska",
            "headers": {},
            "live": False,
            "resume_position_ms": int(resume or 0),
            "duration_ms": vod.duration_ms if vod is not None else None,
            "audio_tracks": vod.tracks if vod is not None else [],
            "audio_track": plan.audio_track if plan else None,
            "start_position_ms": plan.start_s * 1000 if plan else None,
            "title": row.title,
            "subtitle": row.subtitle or row.channel_name or None,
            "poster": None,
            "channel_context": None,
            "handoff": None,
            "drm": None,
        }
        desc.update(await self._runtime_audio(profile_id, {"probe": probe} if probe else {}, consent=True))
        return desc

    async def _probe_recording(self, recording_id: str, path: Any) -> dict[str, Any] | None:
        """Probe a recording's file once per file; ``None`` without ffprobe or when it cannot be read."""
        if self.probe is None or not self.probe.available:
            return None
        stamp = (str(path), path.stat().st_size)
        held = self._recording_probes.get(recording_id)
        if held is not None and held[0] == stamp:
            return held[1]
        found: dict[str, Any] | None = await self.probe.run(str(path), {})
        if found is not None:
            if len(self._recording_probes) >= 256:
                self._recording_probes.clear()
            self._recording_probes[recording_id] = (stamp, found)
        return found

    async def _runtime_audio(self, profile_id: str, extra: dict[str, Any], consent: bool) -> dict[str, Any]:
        preferred = await self._catalog.preferred_languages(profile_id, None)
        info = audio_info(extra)
        return {
            "preferred_audio_languages": preferred,
            "audio_language_required": bool(preferred) and not consent,
            **{
                key: info[key]
                for key in ("audio_languages", "audio_verified", "audio_evidence", "default_audio_language")
            },
        }

    async def _tmdb_matches(
        self, media_type: str, tmdb_id: int
    ) -> tuple[dict[str, Source], Any, list[dict[str, Any]], list[Title]]:
        """Where a TMDB title can be had: (sources, metadata, badges, the rows of enabled sources)."""
        sources = await self._sources.rows_by_id()
        meta = await self._meta.details(media_type, tmdb_id) if self._meta.enabled else None
        badges, matches = await self._availability.badges_for(
            media_type,
            tmdb_id=tmdb_id,
            imdb_id=meta.imdb_id if meta else None,
            title=meta.title if meta else None,
            year=meta.year if meta else None,
            sources=sources,
            is_in_app=self._sources.registry.is_in_app,
        )
        return sources, meta, badges, [m for m in matches if m.source_id in sources and sources[m.source_id].enabled]

    async def _resolve_tmdb(
        self,
        profile_id: str,
        media_type: str,
        tmdb_id: int,
        hub_id: str,
        source_id: str | None,
        client: str,
        https_site: bool,
        variant_id: str | None = None,
        allow_unverified_audio: bool = False,
        audio_track: int | None = None,
        start_position_ms: int | None = None,
        room: RoomScope | None = None,
    ) -> dict[str, Any]:
        sources, meta, badges, matches = await self._tmdb_matches(media_type, tmdb_id)
        if matches and (not source_id or any(m.source_id == source_id for m in matches)):
            row = await self._select_audio(
                profile_id,
                matches,
                variant_id,
                allow_unverified_audio,
                source_id,
                audio_track=audio_track,
                probe=client != "android_tv",
            )
            playback_id = ids.lib_id(row.source_id, row.ext_id) if row.kind == "episode" else hub_id
            return await self._resolve_title(
                profile_id,
                row,
                playback_id,
                client,
                https_site,
                audio_consent=bool(variant_id and allow_unverified_audio),
                audio_track=audio_track,
                start_position_ms=start_position_ms,
                room=room,
            )
        if variant_id:
            raise NotFound("variant does not belong to this title or source")
        chosen = None
        if source_id:
            chosen = next((b for b in badges if b["source_id"] == source_id), None)
            if chosen is None:
                raise NotFound("title not available on that source")
        elif badges:
            chosen = badges[0]
        if chosen is None:
            raise NotFound("no source can play this title")
        if (
            self.content_policy is not None
            and self.content_policy.enabled
            and self.content_policy.is_restricted(await self.content_policy.profile(profile_id))
        ):
            raise Forbidden("External app playback requires owner access", code="content_restricted")
        # hand-off
        svc = sources[chosen["source_id"]]
        link = await self._deeplinks.link_for(
            svc.service_key or "", tmdb_type=media_type, tmdb_id=tmdb_id, title=meta.title if meta else ""
        )
        if link is None:
            raise NotFound("service not in registry")
        return {
            "id": hub_id,
            "mode": PlaybackMode.HANDOFF.value,
            "url": None,
            "mime": None,
            "headers": {},
            "live": False,
            "resume_position_ms": 0,
            "duration_ms": (meta.runtime_min * 60000) if meta and meta.runtime_min else None,
            "title": meta.title if meta else svc.name,
            "subtitle": f"Open in {link.name}",
            "poster": meta.poster if meta else None,
            "channel_context": None,
            "handoff": {
                "service_key": link.service_key,
                "android_package": link.android_package or "",
                "android_uri": link.android_uri,
                "android_component": link.android_component,
                "android_extras": link.android_extras,
                "web_url": link.web_url,
                "supports_title_link": link.supports_title_link,
            },
        }

    # ------------------------------------------------------------------ descriptor
    def _descriptor(
        self,
        profile_id: str,
        src: Source,
        hub_id: str,
        target: StreamTarget,
        client: str,
        https_site: bool,
        *,
        channel_key: str | None,
        title: str,
        subtitle: str | None,
        poster: str | None,
        policy_ref: str | None = None,
        policy_at: datetime | None = None,
        policy_until: datetime | None = None,
        vod: WebVod | None = None,
        kind: str | None = None,
        private: bool = False,
        room: str | None = None,
    ) -> dict[str, Any]:
        mode = decide_mode(
            client=client,
            url=target.url,
            mime=target.mime,
            live=target.live,
            headers=target.headers,
            source_policy=self._sources.proxy_policy(src),
            global_mode=str(self._settings.get("proxy_mode") or "auto"),
            https_site=https_site,
            max_connections=self._sources.max_connections(src),
            active_on_source=self.active.count(src.id),
            cors_ok=self._sources.decrypt_config(src).get("cors", True) is not False,
        )
        if self._governed():
            # Only what this server serves itself is checked again while it plays.
            if target.live:
                mode = PlaybackMode.HLS
            elif mode == PlaybackMode.DIRECT:
                mode = PlaybackMode.PROXY
        from app.services.content_policy import active_principal

        principal = active_principal.get()
        if mode == PlaybackMode.DIRECT and not (principal is not None and principal.can("sources.manage")):
            # An upstream URL carries the source's credentials: a Plex token, an Xtream username
            # and password, a provider's signed query. It goes only to someone who manages the
            # sources and so holds them already. Everyone else plays through this server, whatever
            # the proxy settings say about the route.
            raw_live = target.live and (target.mime or "").lower() == "video/mp2t"
            mode = PlaybackMode.HLS if raw_live else PlaybackMode.PROXY
        # A file the browser cannot decode is served as HLS whatever the routing above chose.
        plan = vod if vod is not None and vod.video is not None else None
        if plan is not None:
            mode = PlaybackMode.HLS
        transcode = str(self._settings.get("transcode_hw") or "none") != "none" and bool(
            self._sources.decrypt_config(src).get("transcode")
        )
        url = target.url
        mime = target.mime
        headers = dict(target.headers)
        # Adult rows stay out of everything nobody asked for by id, a busy answer included.
        shown = None if private else title[:80]
        device = client if client == "android_tv" else "web"
        self._admit(principal, profile_id, policy_ref or hub_id, shown, kind, device)
        if mode in (PlaybackMode.PROXY, PlaybackMode.HLS):
            token = self.tokens.issue(
                StreamGrant(
                    profile_id=profile_id,
                    content_ref=policy_ref or hub_id,
                    profile_version=principal.token_version if principal is not None else None,
                    session_id=principal.session_id if principal is not None else None,
                    policy_at=policy_at.isoformat() if policy_at else None,
                    policy_until=policy_until.isoformat() if policy_until else None,
                    source_id=src.id,
                    url=target.url,
                    headers=target.headers,
                    mode=mode.value,
                    live=target.live,
                    channel_key=channel_key if mode == PlaybackMode.PROXY else None,
                    transcode=transcode,
                    vod_video=plan.video if plan else None,
                    vod_codec=plan.codec if plan else None,
                    vod_width=plan.width if plan else None,
                    audio_track=plan.audio_track if plan else None,
                    start_s=plan.start_s if plan else 0,
                    title=shown,
                    kind=kind,
                    user_id=(principal.user_id or None) if principal is not None else None,
                    user_version=principal.user_version if principal is not None else None,
                    client=device,
                    room=room if plan is not None else None,
                )
            )
            if mode == PlaybackMode.HLS:
                url = absolute(f"/api/v1/stream/{token}/index.m3u8")
                mime = "application/x-mpegURL"
            else:
                url = absolute(f"/api/v1/stream/{token}")
            headers = {}
        return {
            "id": hub_id,
            "mode": mode.value,
            "url": url,
            "mime": mime,
            "headers": headers,
            "live": target.live,
            "resume_position_ms": int(target.resume_position_ms or 0),
            "duration_ms": vod.duration_ms if vod is not None else target.duration_ms,
            "audio_tracks": vod.tracks if vod is not None else [],
            "audio_track": plan.audio_track if plan else None,
            "start_position_ms": plan.start_s * 1000 if plan else None,
            "title": title,
            "subtitle": subtitle,
            "poster": poster,
            "channel_context": None,
            "handoff": None,
            "drm": None,
        }


def is_library(src: Source) -> bool:
    return SourceType(src.type) in LIBRARY_SOURCE_TYPES
