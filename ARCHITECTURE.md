# StreamHub — Architecture

Self-hosted unified streaming hub. One Docker container on Unraid serves the API and the web client; a native Android TV app talks to the same API.

```
streamhub/
├─ contracts/openapi.yaml   # single source of truth for the HTTP API (generate Kotlin + TS clients)
├─ design/tokens.json       # shared design tokens → Kotlin (Compose) + CSS variables (web)
├─ design/BRIEF.md          # TV design brief + UAT acceptance criteria
├─ hub-server/              # FastAPI backend (Python 3.12+)
├─ tv-app/                  # Android TV, Kotlin, Compose for TV, Media3 ExoPlayer
├─ web-app/                 # React 19 + Vite + TS, Norigin spatial nav, hls.js / mpegts.js
├─ deploy/                  # Dockerfile, docker-compose.yml, Unraid CA template
└─ docs/                    # LEGAL.md, DEEPLINKS.md, user docs
```

## Principles
- **SOLID / ports & adapters.** `domain/` has entities + ports (Protocols). `adapters/` has one package per source type. Services depend on ports, never on adapters. New source types are added by registering an adapter; no core code changes (OCP).
- **Clients never talk to Plex/Xtream/TMDB directly.** The server resolves metadata and playback; clients get a `PlaybackDescriptor` and play it. This keeps credentials on the server and makes "press Play → stream starts" identical for every in-app source.
- **In-app sources** (Plex, Jellyfin, Xtream live/VOD/series, M3U) play inside the app via ExoPlayer / hls.js / mpegts.js. **Hand-off sources** (Netflix, Hulu, Disney+, …) are launched through the deep-link registry; the UI labels them "Open in app".
- **Profiles own state.** Favorites, resume positions, theme, language filters, parental PIN, and watchlist are per profile.

## Backend (hub-server)
FastAPI · Pydantic v2 · SQLAlchemy 2.0 async · Alembic · SQLite (WAL) under `/config` · httpx · lxml · APScheduler · ffmpeg (subprocess) for optional proxy/remux/transcode.

```
hub-server/app/
├─ domain/            # entities (Source, Title, Channel, Programme, Availability, Profile…) + ports
│   └─ ports.py       # SourceProvider, CatalogProvider, AvailabilityProvider, EpgProvider, Repository protocols
├─ adapters/
│   ├─ plex/          # PIN auth, /library/sections, /hubs/search, direct-play URL, timeline
│   ├─ jellyfin/      # Quick Connect / password auth, Items, PlaybackInfo
│   ├─ xtream/        # player_api.php actions, stream URL builder, xmltv.php
│   ├─ m3u/           # streaming #EXTINF parser, #EXTVLCOPT, catchup attrs, url-tvg
│   ├─ xmltv/         # iterparse EPG → programme table
│   ├─ tmdb/          # search/multi, details, images, watch/providers (JustWatch attribution)
│   ├─ deeplink/      # service registry (android pkg, title URI template, web URL template)
│   └─ language/      # language inference chain: tvg-language → iptv-org db → country → group-title → flag → name
├─ services/          # SourceRegistry, CatalogService, AvailabilityService, EpgService, PlaybackResolver, FavoritesService, SyncScheduler
├─ api/               # routers mirroring contracts/openapi.yaml; auth (JWT + refresh), device-code pairing for TV
├─ proxy/             # StreamProxy: passthrough, HLS remux (-c copy), transcode (libx264 / VAAPI), shared upstream per channel
└─ infra/             # db, settings, crypto (Fernet for source configs), logging with URL redaction
```

### Playback resolution (the "press Play" path)
1. Client `POST /playback/resolve {kind, id, profile_id}`.
2. `PlaybackResolver` asks the owning adapter for a `PlaybackDescriptor`:
   - `mode`: `direct` | `proxy` | `hls` | `handoff`
   - `url`, `headers` (User-Agent/Referer from `#EXTVLCOPT` or Xtream), `mime`, `resume_position_ms`, `drm: null`
   - for `handoff`: `android_intent {package, uri, extras}` + `web_url`
3. Server decides `proxy`/`hls` when: http stream on https site, CORS-blocked, codec not browser-decodable (web only), or source `max_connections == 1` and >1 client.
4. Client plays. Progress is reported to `POST /progress` every 10 s and on pause/stop; for Plex it is forwarded to `/:/timeline`.

### IPTV feature parity (must-have in v1)
Favorites (channels, VOD, series, shows across all sources) · groups/categories · language filter chips with per-group override · full EPG grid + now/next on tiles · catch-up/replay where source supports it · channel numbers, number-key jump, channel up/down while playing, last-channel toggle · resume positions · global search · parental PIN per group/rating · sleep timer · multiple profiles · per-source headers/UA · hide/reorder groups · recently watched · playlist auto-refresh schedule · EPG auto-refresh · stream reconnect with backoff · aspect ratio / audio track / subtitle selection · picture-in-picture on web.

## Data model (SQLite)
`source`, `profile`, `title`, `availability`, `channel`, `channel_group`, `programme`, `favorite(profile_id, kind, ref_id, sort)`, `progress`, `watchlist`, `service_registry`, `theme`, `setting`, `sync_run`, `disclaimer_acceptance(profile_id, version, accepted_at)`.

## Android TV app (tv-app)
Kotlin · Compose BOM 2026.x · `androidx.tv:tv-material:1.1.0` · `tv-foundation:1.0.0` · Media3 1.11.x (+ ffmpeg decoder ext for AC-3) · Hilt · Ktor client (generated from OpenAPI) · Coil 3 · DataStore. Standard `LazyRow`/`LazyColumn` with `Modifier.focusRestorer()` and a custom `BringIntoViewSpec`; no `TvLazy*`. Left rail nav (Home, Search, Live TV, Guide, Movies, Shows, Favorites, Sources, Settings). Clean-architecture modules: `:core:ui`, `:core:data`, `:core:player`, `:feature:*`.

## Web app (web-app)
React 19 · Vite · TypeScript · TanStack Query · React Router · `@noriginmedia/norigin-spatial-navigation-react` · hls.js · mpegts.js · Tailwind v4 (`@theme` from tokens.json) · theme switch via `data-theme` on `<html>`.

## Deployment
Single image: Python app serves API under `/api/v1` and the built web app at `/`. Env: `PUID`, `PGID`, `TZ`, `TMDB_API_KEY` (optional; can be set in UI), `SECRET_KEY`. Volumes: `/config`, optional `/transcode`. Optional device `/dev/dri`. Port `8787`.

## Legal
First-run disclaimer gate (see docs/LEGAL.md) must be accepted before any IPTV/M3U source can be added. TMDB + JustWatch attribution in About and next to availability data.
