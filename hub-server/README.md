# StreamHub hub-server

FastAPI backend for StreamHub. Implements `contracts/openapi.yaml` under `/api/v1` and serves the
built web client from `/app/web-dist` at `/` (with SPA fallback) when present.

## Quick start (dev)

```bash
cd hub-server
python3.12 -m venv .venv            # 3.12+ (3.13 tested)
.venv/bin/pip install -e '.[dev]'

export CONFIG_DIR=$PWD/dev-config   # default /config
export ADMIN_PASSWORD=change-me     # optional; otherwise generated and logged ONCE on first boot
# export TMDB_API_KEY=...           # optional; can also be set via PUT /api/v1/settings/tmdb_key

# demo content without real credentials (M3U fixture + generated 30 h XMLTV guide)
.venv/bin/python scripts/demo_seed.py

.venv/bin/uvicorn app.main:app --port 8787
```

- API docs: http://localhost:8787/api/docs (OpenAPI JSON at `/api/openapi.json`)
- Health: `GET /api/v1/health`
- Log in: `GET /api/v1/profiles` → `POST /api/v1/auth/login {profile_id, password}`

## Configuration (environment)

| var | default | |
|---|---|---|
| `CONFIG_DIR` | `/config` | SQLite DB (WAL), secret key, playlists, caches |
| `SECRET_KEY` | generated → `CONFIG_DIR/secret.key` | derives Fernet / JWT / signing keys |
| `ADMIN_PASSWORD` | generated | admin profile password on first boot |
| `TMDB_API_KEY` | – | v3 key or v4 read token |
| `WEB_DIST` | `/app/web-dist` | built web client |
| `TRANSCODE_DIR` | `/transcode` if present, else `CONFIG_DIR/transcode` | ffmpeg HLS output |
| `CORS_ORIGINS` | `http://localhost:5173,…` | comma separated (dev) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | reverse proxies (addresses or CIDRs) whose `X-Forwarded-For` names the real client; the variable uvicorn reads too |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | `text` for dev |
| `ENABLE_SCHEDULER` | `true` | catalog refresh (5 min tick) + EPG refresh (30 min tick) |
| `FFMPEG_PATH`, `VAAPI_DEVICE` | `ffmpeg`, `/dev/dri/renderD128` | remux / transcode |
| `FFPROBE_PATH` | `ffprobe` | reads a VOD file's codecs and audio tracks before web playback; without it files are served unprobed |
| `IMAGE_CACHE_MAX_MB` | `512` | cap for proxied artwork in `CONFIG_DIR/cache/images` (oldest evicted first; `0` disables) |
| `RECORDINGS_DIR` | – | where recordings are written; recording is off until this names an existing, writable directory |
| `RECORDINGS_MIN_FREE_GB` | `50` | recordings stop, and none start, when free space under `RECORDINGS_DIR` falls below this |

Migrations run automatically at startup (`alembic upgrade head`). Manual: `.venv/bin/alembic upgrade head`
(uses `sqlalchemy.url` from `alembic.ini`, override with `-x` or edit for your DB path).

## Layout (ports & adapters)

```
app/
  domain/      entities.py (dataclasses) · ports.py (typing.Protocol: SourceProvider, LiveProvider, PlaybackProvider,
               EpgProvider, MetadataProvider, LanguageInferrer, DeeplinkProvider, Cache, …)
  adapters/    plex · jellyfin · xtream · m3u · xmltv · tmdb · deeplink · language · streaming_service
               __init__.py = the ONLY place adapters are registered with the SourceRegistry
  services/    registry (SourceType → factory) · sources · catalog · availability · channels (+EPG grid)
               playback (PlaybackResolver, stream tokens, mode decision) · sync (+APScheduler) · home
               user_state (favorites, watchlist, progress, home layout) · themes · settings · auth · disclaimer
  api/         schemas (pydantic, mirrors the contract) · deps · routers/*
  proxy/       StreamProxy: passthrough, shared upstream fan-out, ffmpeg HLS remux/transcode
  infra/       settings (pydantic-settings) · db (async SQLAlchemy + Alembic) · models · crypto (Fernet/scrypt)
               logging (JSON + secret redaction filter) · cache (SQLite TTL) · http (retry/backoff)
  container.py composition root
```

Services import only `app.domain.ports` / entities and use `isinstance(provider, <CapabilityProtocol>)` for optional
capabilities. Adding a source type = new adapter package + one `registry.register(...)` line in
`app/adapters/__init__.py`.

## Behaviour notes

- **Ids**: `tmdb:movie:603`, `tmdb:tv:1399`, `lib:<source>:<ext>`, `ch:<source>:<ext>`, `prog:<n>`. Xtream panels
  number movies and series independently, so their series, seasons and episodes use `s:<id>`, `s:<id>:s<n>` and
  `e:<id>` as `<ext>`; movies and channels keep the panel's id. A sync may replace a row's TMDB/IMDb id, never clear it.
- **Presentation** (`adapters/xtream/presentation.py`): at sync, Xtream titles lose provider tags and the year suffix,
  categories become shelves (`group_name`), and `language`, `quality` and `adult` are stored on the row; raw names stay
  in `extra`. Per-source config: `default_language`, `shelf_map` and `group_map` (raw name → display name),
  `hidden_groups` (live groups not stored), `adult_categories`. `default_language` also applies to Plex/Jellyfin sources.
  Title language filters read `title.language`; it is a hint, never audio evidence.
- **Adult rows** (`services/adult.py`) are left out of home, search, browse and suggestions unless the admin profile
  passes `include_adult=true`. This hides; it does not authorize access by id.
- **Rating ceilings** (`services/content_policy.py`): a user's limits (`rating_ceiling_movie`, `rating_ceiling_tv`,
  `allow_unrated`, from the role or per-user overrides) run the content-policy engine for that user's requests, whatever
  `CONTENT_POLICY_ENABLED` says; that flag only keeps the older owner-PIN regime. Ratings are US certifications from
  TMDB. A background job (`services/classifier.py`, every 2 minutes, 60 titles a run, no-op without a TMDB key) keeps
  them fresh for every title with a TMDB id; `POST /api/v1/settings/classification/run` runs one batch on demand.
- **Remote access** (`services/network.py`): a user without `remote.access` is served only from the `home_networks`
  setting (private, loopback, link-local and `100.64.0.0/10` ranges by default); elsewhere every call but
  `GET /auth/me` answers 403 `remote_access_denied`. The client address is the connection's peer, or the
  `X-Forwarded-For` hop a proxy listed in `FORWARDED_ALLOW_IPS` appended. Behind a reverse proxy or tunnel that is
  **not** listed there, every client appears to come from the proxy's own address, which is usually a home one.
- **TV app release**: `GET /api/v1/app/tv` (no sign-in) answers from `tv-release.json` in `WEB_DIST/downloads/`, next
  to the APK, which is served at `/downloads/<file>`. See "TV app release manifest" below. No manifest, or an invalid
  one, is a 404, which the TV app reads as "no update service".
- **Disclaimer gate**: `POST /sources` with `xtream`/`m3u` → 412 until the profile accepted version 1.0 (`docs/LEGAL.md`).
- **Secrets**: source configs are Fernet-encrypted at rest; Xtream credentials are never stored in catalog rows
  (URLs rebuilt at play time); Plex artwork goes through `/api/v1/images/{signed}` so tokens never reach clients
  outside playback URLs; a log filter redacts registered secrets and credential URL parts.
- **Proxied artwork**: for a source with `proxy: always`, posters, backdrops and channel logos use the same signed
  image route (clients cannot reach such a source's hosts); responses are cached on disk and only image types are served.
- **M3U upload**: `config.file` = playlist text → stored as `CONFIG_DIR/playlists/upload-*.m3u`.
- **Playback modes** (`services/playback.py::decide_mode`): source `proxy: always|hls|never|auto`, source `cors: false`,
  global `proxy_mode`, `X-Forwarded-Proto: https` + `http://` upstream, custom headers on web, and
  `max_connections == 1` with an active stream → `proxy`. Stream tokens are Fernet tokens (TTL 6 h).
- **Proxy**: live channels share one upstream per channel (fan-out; closed 10 s after last client), reconnect with
  exponential backoff; HLS upstream playlists rewritten through the proxy; `hls` mode runs
  `ffmpeg -c copy -f hls -hls_time 2 -hls_list_size 6 -hls_flags delete_segments`, or transcodes
  (libx264 / VAAPI / QSV / NVENC) when `transcode_hw != none` and the source sets `transcode: true`. Idle sessions reaped after 60 s.
- **Channel lists**: every `GET /channels` stores its ordered result under `list_id`; `GET /channels?list_id=` returns
  it again (zapping). Channel numbers = per-source offset (+1000 per IPTV source) + tvg-chno/position; user override wins.
- **Progress**: `stopped` with `position_ms >= duration_ms` (or ≥95%) marks watched; forwarded to Plex `/:/timeline`
  and Jellyfin `/Sessions/Playing/*`.

## TV app release manifest

`WEB_DIST/downloads/tv-release.json`, written when an APK is published:

```json
{
  "version_code": 14,
  "version_name": "1.4.0",
  "apk": "streamhub-tv-1.4.0.apk",
  "sha256": "<the APK's SHA-256, 64 hex digits>"
}
```

| field | |
|---|---|
| `version_code` | integer ≥ 1, the build's Android `versionCode`; a TV offers the update when it is higher than its own |
| `version_name` | what to call the build, 1–64 characters |
| `apk` | file name of the APK in the same directory: letters, digits, `.`, `_`, `-`, ending in `.apk`. A name, not a path or a URL, and the file must exist |
| `sha256` | SHA-256 of that file (`sha256sum <file>`); the TV refuses a download that does not match |

Other fields are ignored. The response is `{"version_code", "version_name", "apk_url": "/downloads/<apk>", "sha256"}`.
The manifest is read on each request, so publishing is two file writes: the APK first, then the manifest.

## Quality

```bash
.venv/bin/ruff check app tests scripts alembic
.venv/bin/mypy app
.venv/bin/pytest -q
```

Tests use respx for all outbound HTTP (no network) and include a 200k-channel streaming M3U parse with a memory bound.
