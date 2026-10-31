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
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | `text` for dev |
| `ENABLE_SCHEDULER` | `true` | catalog refresh (5 min tick) + EPG refresh (30 min tick) |
| `FFMPEG_PATH`, `VAAPI_DEVICE` | `ffmpeg`, `/dev/dri/renderD128` | remux / transcode |

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

- **Ids**: `tmdb:movie:603`, `tmdb:tv:1399`, `lib:<source>:<ext>`, `ch:<source>:<ext>`, `prog:<n>`.
- **Disclaimer gate**: `POST /sources` with `xtream`/`m3u` → 412 until the profile accepted version 1.0 (`docs/LEGAL.md`).
- **Secrets**: source configs are Fernet-encrypted at rest; Xtream credentials are never stored in catalog rows
  (URLs rebuilt at play time); Plex artwork goes through `/api/v1/images/{signed}` so tokens never reach clients
  outside playback URLs; a log filter redacts registered secrets and credential URL parts.
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

## Quality

```bash
.venv/bin/ruff check app tests scripts alembic
.venv/bin/mypy app
.venv/bin/pytest -q
```

Tests use respx for all outbound HTTP (no network) and include a 200k-channel streaming M3U parse with a memory bound.
