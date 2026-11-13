"""Composition root: builds infra, adapters and services and wires them together.

This is the only place (besides ``app.adapters.register_builtin_adapters``) that
names concrete adapter classes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.adapters import register_builtin_adapters
from app.adapters.deeplink.registry import DeeplinkRegistry
from app.adapters.language.inferrer import LanguageChain
from app.adapters.plex.provider import PlexLinkFlow
from app.adapters.seerr import SeerrConnection
from app.adapters.tmdb.client import TmdbClient
from app.adapters.xmltv.parser import XmltvEpgProvider
from app.domain.ports import AdapterContext, Cache
from app.infra.cache import SqlCache
from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.http import make_client
from app.infra.image_cache import ImageCache
from app.infra.settings import AppSettings
from app.proxy.capture import Capture
from app.proxy.dvr import DvrManager
from app.proxy.stream_proxy import StreamProxy
from app.services.auth import AuthService
from app.services.availability import AvailabilityService
from app.services.catalog import CatalogService
from app.services.channels import ChannelService
from app.services.classification import ClassificationStore
from app.services.classifier import ClassificationJob
from app.services.content_policy import ContentPolicy
from app.services.disclaimer import DisclaimerService
from app.services.google_auth import GoogleAuthService
from app.services.home import HomeService
from app.services.invites import InvitesService
from app.services.media_probe import MediaProbe
from app.services.notices import NoticeService
from app.services.owner_pin import OwnerPinService
from app.services.owner_reauth import OwnerReauthService
from app.services.playback import ActiveStreams, PlaybackResolver, StreamTokens
from app.services.plex_auth import PlexAuthService
from app.services.recorder import RecorderService
from app.services.registry import SourceRegistry
from app.services.requests import RequestService
from app.services.room_hub import RoomHub, RoomTokens
from app.services.room_playback import RoomPlayback
from app.services.rooms import RoomRegistry
from app.services.services_registry import ServiceRegistryService
from app.services.sessions import SessionsService
from app.services.settings_service import SettingsService
from app.services.sources import SourceService
from app.services.streams import GrantGuard, StreamRegistry
from app.services.sync import SyncScheduler, SyncService
from app.services.themes import ThemeService
from app.services.user_state import (
    ClientPrefsService,
    FavoritesService,
    HomeLayoutService,
    ProgressService,
    WatchlistService,
)
from app.services.users import UsersService

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: AppSettings
    db: Database
    crypto: Crypto
    cache: Cache
    adapter_ctx: AdapterContext
    registry: SourceRegistry
    app_settings: SettingsService
    disclaimer: DisclaimerService
    auth: AuthService
    users: UsersService
    sessions: SessionsService
    invites: InvitesService
    google_auth: GoogleAuthService
    plex_auth: PlexAuthService
    language: LanguageChain
    tmdb: TmdbClient
    sources: SourceService
    availability: AvailabilityService
    owner_reauth: OwnerReauthService
    owner_pin: OwnerPinService
    content_policy: ContentPolicy
    classifier: ClassificationJob
    catalog: CatalogService
    channels: ChannelService
    favorites: FavoritesService
    watchlist: WatchlistService
    progress: ProgressService
    layout: HomeLayoutService
    client_prefs: ClientPrefsService
    themes: ThemeService
    service_registry: ServiceRegistryService
    deeplinks: DeeplinkRegistry
    plex_link: PlexLinkFlow
    sync: SyncService
    scheduler: SyncScheduler
    home: HomeService
    active: ActiveStreams
    tokens: StreamTokens
    streams: StreamRegistry
    guard: GrantGuard
    playback: PlaybackResolver
    proxy: StreamProxy
    image_cache: ImageCache
    requests: RequestService
    notices: NoticeService
    rooms: RoomHub
    room_playback: RoomPlayback
    recorder: RecorderService
    http_factory: Any
    capture: Capture | None = None


def build_container(settings: AppSettings, http_factory: Any = None, cache: Cache | None = None) -> Container:
    http_factory = http_factory or (lambda **kw: make_client(**kw))
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_url)
    crypto = Crypto(settings.resolved_secret_key)
    cache = cache or SqlCache(db)
    client_id = "streamhub-" + crypto.sign_json({"cid": 1}).split(".")[1][:16]
    ctx = AdapterContext(
        http_factory=http_factory,
        cache=cache,
        data_dir=settings.config_dir,
        client_id=client_id,
        product=settings.plex_product,
    )
    registry = SourceRegistry(ctx)
    register_builtin_adapters(registry)
    app_settings = SettingsService(db, crypto, settings.tmdb_api_key)
    disclaimer = DisclaimerService(db)
    auth = AuthService(db, crypto, settings.access_token_ttl_s, settings.refresh_token_ttl_s)
    # One users service, the one the auth service validates against, now able to notify.
    auth.users = UsersService(db, sessions=auth.sessions, setting=app_settings.get, http_factory=http_factory)
    content_policy = ContentPolicy(db, settings)
    auth.content_policy = content_policy
    owner_pin = OwnerPinService(auth, db, crypto)
    auth.owner_pin = owner_pin
    content_policy.owner_pin = owner_pin
    language = LanguageChain(ctx, settings.iptv_org_channels_url)
    tmdb = TmdbClient(ctx, app_settings.tmdb_key, ClassificationStore(db).record)
    sources = SourceService(db, crypto, registry, disclaimer, language)
    availability = AvailabilityService(db, tmdb)
    catalog = CatalogService(db, crypto, sources, availability, tmdb)
    catalog.content_policy = content_policy
    channels = ChannelService(db, sources)
    channels.image_fn = catalog.image_fn
    favorites = FavoritesService(db)
    watchlist = WatchlistService(db)
    progress = ProgressService(db, sources)
    layout = HomeLayoutService(db)
    client_prefs = ClientPrefsService(db)
    themes = ThemeService(db)
    service_registry = ServiceRegistryService(db)
    deeplinks = DeeplinkRegistry(ctx, service_registry.get)
    plex_link = PlexLinkFlow(ctx)
    sync = SyncService(db, sources, XmltvEpgProvider(ctx), app_settings)

    def request_manager() -> SeerrConnection | None:
        configured = app_settings.request_manager()
        return SeerrConnection(http_factory, *configured) if configured else None

    requests = RequestService(db, availability, sources, tmdb, request_manager)
    requests.content_policy = content_policy
    sync.after_catalog_sync = requests.reconcile
    # Schedules only until the proxy exists: the capture backend is assigned below.
    recorder = RecorderService(
        db, channels, recordings_dir=settings.recordings_dir, min_free_gb=settings.recordings_min_free_gb
    )
    recorder.content_policy = content_policy
    sync.after_epg_refresh = recorder.apply_rules
    classifier = ClassificationJob(db, tmdb, app_settings)
    scheduler = SyncScheduler(sync, requests.reconcile, recorder.run_tick, classifier.run_batch)
    home = HomeService(catalog, channels, progress, layout, sources, availability, tmdb)
    active = ActiveStreams()
    tokens = StreamTokens(crypto, settings.stream_token_ttl_s)
    playback = PlaybackResolver(
        sources=sources,
        catalog=catalog,
        channels=channels,
        availability=availability,
        progress=progress,
        settings=app_settings,
        deeplinks=deeplinks,
        meta=tmdb,
        tokens=tokens,
        active=active,
    )

    playback.content_policy = content_policy
    playback.auth = auth
    streams = StreamRegistry(db)
    playback.streams = streams
    auth.sessions.streams = streams

    def proxy_client(**kw: Any) -> httpx.AsyncClient:
        client: httpx.AsyncClient = http_factory(**kw)
        return client

    proxy = StreamProxy(
        proxy_client,
        settings.transcode_path,
        ffmpeg=settings.ffmpeg_path,
        vaapi_device=settings.vaapi_device,
        on_start=active.inc,
        on_stop=active.dec,
        dvr=DvrManager(settings.dvr_dir, enabled=settings.dvr_enabled, reserve_root=settings.dvr_reserve_dir),
    )
    playback.probe = MediaProbe(settings.ffprobe_path)
    playback.probe_slot = proxy.probe_slot
    playback.pin = proxy.pin
    playback.recorder = recorder
    capture: Capture | None = None
    if settings.recordings_dir:
        # Recording is configured: captures run through the proxy's shared upstreams and admission.
        capture = Capture(
            proxy,
            sources,
            db,
            recordings_dir=settings.recordings_dir,
            ffmpeg=settings.ffmpeg_path,
            min_free_gb=settings.recordings_min_free_gb,
        )
        recorder.capture = capture
        streams.external.append(capture.user_streams)
    google_auth = GoogleAuthService(settings, auth, lambda: app_settings.get("signup_mode"))

    async def plex_owner_tokens() -> list[str]:
        """The Plex account tokens behind the linked Plex servers: the owner's, by construction."""
        rows = [r for r in await sources.list() if r.type == "plex" and r.enabled]
        return [str(sources.decrypt_config(r).get("account_token") or "") for r in rows]

    plex_auth = PlexAuthService(
        ctx,
        auth,
        app_settings.get,
        plex_owner_tokens,
        # The legacy owner-PIN regime signs in with Google and the PIN, nothing else.
        available=lambda: not content_policy.enabled,
    )
    rooms = RoomHub(RoomRegistry(), RoomTokens(crypto))
    return Container(
        settings=settings,
        db=db,
        crypto=crypto,
        cache=cache,
        adapter_ctx=ctx,
        registry=registry,
        app_settings=app_settings,
        disclaimer=disclaimer,
        auth=auth,
        users=auth.users,
        sessions=auth.sessions,
        invites=InvitesService(db),
        google_auth=google_auth,
        plex_auth=plex_auth,
        language=language,
        tmdb=tmdb,
        sources=sources,
        availability=availability,
        catalog=catalog,
        content_policy=content_policy,
        classifier=classifier,
        owner_reauth=OwnerReauthService(auth, google_auth),
        owner_pin=owner_pin,
        channels=channels,
        favorites=favorites,
        watchlist=watchlist,
        progress=progress,
        layout=layout,
        client_prefs=client_prefs,
        themes=themes,
        service_registry=service_registry,
        deeplinks=deeplinks,
        plex_link=plex_link,
        sync=sync,
        scheduler=scheduler,
        home=home,
        active=active,
        tokens=tokens,
        streams=streams,
        guard=GrantGuard(auth, content_policy, streams),
        playback=playback,
        proxy=proxy,
        image_cache=ImageCache(settings.image_cache_dir, settings.image_cache_max_mb * 1024 * 1024),
        requests=requests,
        notices=NoticeService(db),
        rooms=rooms,
        room_playback=RoomPlayback(
            playback, proxy, db=db, sources=sources, content_policy=content_policy, settings=app_settings, hub=rooms
        ),
        recorder=recorder,
        http_factory=http_factory,
        capture=capture,
    )


async def startup(c: Container, *, start_background: bool = True) -> None:
    await c.db.migrate()
    await c.app_settings.load()
    c.proxy.paused = bool(c.app_settings.get("playback_recovery_paused"))
    await c.service_registry.seed()
    generated = await c.auth.ensure_admin(c.settings.admin_password)
    if generated and not c.settings.google_auth_required:
        # Logged exactly once, at first boot. Deliberately bypasses the redaction registry.
        logging.getLogger("streamhub.bootstrap").warning(
            "Admin profile created. Generated admin password (shown once): %s", generated
        )
    await c.sync.reset_stuck()
    if start_background:
        c.proxy.start()
        c.rooms.start()
        if c.settings.enable_scheduler:
            c.scheduler.start()


async def shutdown(c: Container) -> None:
    c.scheduler.shutdown()
    await c.rooms.shutdown()
    await c.room_playback.shutdown()
    await c.recorder.shutdown()
    if c.capture is not None:
        await c.capture.shutdown()
    await c.sync.wait_idle()
    await c.proxy.shutdown()
    await c.db.dispose()
