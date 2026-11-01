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
from app.adapters.tmdb.client import TmdbClient
from app.adapters.xmltv.parser import XmltvEpgProvider
from app.domain.ports import AdapterContext, Cache
from app.infra.cache import SqlCache
from app.infra.crypto import Crypto
from app.infra.db import Database
from app.infra.http import make_client
from app.infra.settings import AppSettings
from app.proxy.dvr import DvrManager
from app.proxy.stream_proxy import StreamProxy
from app.services.auth import AuthService
from app.services.availability import AvailabilityService
from app.services.catalog import CatalogService
from app.services.channels import ChannelService
from app.services.classification import ClassificationStore
from app.services.content_policy import ContentPolicy
from app.services.disclaimer import DisclaimerService
from app.services.google_auth import GoogleAuthService
from app.services.home import HomeService
from app.services.owner_pin import OwnerPinService
from app.services.owner_reauth import OwnerReauthService
from app.services.playback import ActiveStreams, PlaybackResolver, StreamTokens
from app.services.registry import SourceRegistry
from app.services.services_registry import ServiceRegistryService
from app.services.settings_service import SettingsService
from app.services.sources import SourceService
from app.services.sync import SyncScheduler, SyncService
from app.services.themes import ThemeService
from app.services.user_state import (
    ClientPrefsService,
    FavoritesService,
    HomeLayoutService,
    ProgressService,
    WatchlistService,
)

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
    google_auth: GoogleAuthService
    language: LanguageChain
    tmdb: TmdbClient
    sources: SourceService
    availability: AvailabilityService
    owner_reauth: OwnerReauthService
    owner_pin: OwnerPinService
    content_policy: ContentPolicy
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
    playback: PlaybackResolver
    proxy: StreamProxy
    http_factory: Any


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
    scheduler = SyncScheduler(sync)
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
    google_auth = GoogleAuthService(settings, auth)
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
        google_auth=google_auth,
        language=language,
        tmdb=tmdb,
        sources=sources,
        availability=availability,
        catalog=catalog,
        content_policy=content_policy,
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
        playback=playback,
        proxy=proxy,
        http_factory=http_factory,
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
        if c.settings.enable_scheduler:
            c.scheduler.start()


async def shutdown(c: Container) -> None:
    c.scheduler.shutdown()
    await c.sync.wait_idle()
    await c.proxy.shutdown()
    await c.db.dispose()
