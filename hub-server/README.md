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
| `SECRET_KEY` | generated → `CONFIG_DIR/secret.key` | derives Fernet / JWT / signing keys; at least 32 characters when set, or the server does not start (`SECRET_KEY_ALLOW_SHORT=true` keeps a shorter one an existing install already encrypted with) |
| `ADMIN_PASSWORD` | generated | admin profile password on first boot |
| `TMDB_API_KEY` | – | v3 key or v4 read token |
| `WEB_DIST` | `/app/web-dist` | built web client |
| `TRANSCODE_DIR` | `/transcode` if present, else `CONFIG_DIR/transcode` | ffmpeg HLS output |
| `CORS_ORIGINS` | none | comma separated origins, other than the server's own, allowed to call the API with credentials |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | reverse proxies (addresses or CIDRs) whose `X-Forwarded-For` names the real client; the variable uvicorn reads too |
| `API_DOCS_ENABLED` | `false` | serve `/api/docs` and `/api/openapi.json`; off unless set, so a public host does not describe its API to whoever asks |
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
- **Channel groups** have two levels. Without config a live group's name is the provider's with its country tag and
  quality marks removed, in title case: listed abbreviations (`ABC`, `NFL`, `PPV`, `ESPN`, …) and any two or three
  letters with no vowel stay in capitals, and a final `Sport`, `Movie`, `Kid` or `Documentary` reads in the plural.
  `group_map` renames a group, merges groups (several raw names → one display name → one group) and nests them: a
  value written `Parent / Child` puts the group under a parent. Only the first ` / ` separates; anything after it is
  part of the child's name, and a slash with no space beside it is part of a name.

  ```json
  {"group_map": {
    "US| NETWORK-A ᴴᴰ": "Locals",
    "US| NETWORK-B ᴴᴰ": "Locals",
    "US| SPORT ᴴᴰ": "Sports",
    "US| LEAGUE-ONE PPV": "Sports / League One",
    "US| LEAGUE-ONE REPLAY": "Sports / League One Replays",
    "US| COLLEGE HOOPS PPV": "Sports / College Basketball"
  }}
  ```

  gives two top-level groups: `Locals` (both networks) and `Sports`, which holds its own channels and three children.
  A `group_map` or `hidden_groups` key names a group by the provider's name or by its default name, whatever the
  case; the default name from before abbreviations and plurals were handled (`Sport`, `Abc`) still matches. The mapping
  is applied at sync, so a change shows after the source's next sync. A provider's own ` / ` never nests (it is shown as
  `/`); an M3U playlist's `group-title` is used as written, so `Parent / Child` there nests too.
  `GET /channels/groups` lists each top-level group followed by its children (`parent`, `path`); a parent is listed
  even when nothing is mapped to it directly, and its `channel_count` includes its children's. `GET /channels?group=`
  takes an id or a path: a parent brings its children, a child is named by `Parent / Child` or by its bare name while
  only one path ends in it. The name is the hierarchy: there is no parent column. A parent is a stored group like any
  other (a sync creates its row with the first child), so `PATCH /channels/groups/{id}` works on it unchanged: hiding
  a parent hides its children without touching their own setting, a child can be hidden alone, and a parent's
  `language_override` reaches the children that set none.
- **Event slots** (`services/event_slots.py`): a sports panel renames numbered channels for each game instead of
  sending a guide (`XX (NETWORK 001) | Sport: Away at Home (2026-10-02 14:50:00)`, or dateless
  `LEAGUE | 02 - 1pm Away at Home`). Such a channel is shown as `NETWORK 001`, and the game in its name is its programme
  while live (from the embedded start for `event_duration_minutes`, default 210) or still to come; a name whose time
  has passed means nothing. The embedded time is read in the source's `event_timezone` (IANA name, default `UTC`).
  Both keys are per-source config. The stored name is not rewritten. A programme that only repeats its channel's name,
  or is titled like a slot, is the manager's filler for a missing guide: never now/next, an event or a rule match.
- **Adult rows** (`services/adult.py`) are left out of home, search, browse and suggestions unless the admin profile
  passes `include_adult=true`. This hides; it does not authorize access by id.
- **Shelves** (`services/catalog.py`, `services/home.py`): `GET /browse/{section}/shelves` lists shelves with their first
  titles, New Releases first, then by size (a shelf with nothing new in 30 days counts half). Home runs Continue
  Watching, On Now, New Releases, up to eight shelves alternating shows and movies, the personal rows, Recently Added.
  The `home_shelves` setting (ordered list of shelf names, `shows:Name` or `movies:Name` to name one side) replaces
  the automatic eight. `PATCH /settings` writes it (`system.manage`; up to 24 entries, null or `[]` clears it); entries
  no shelf has are kept and listed in `home_shelves_unknown`. The hero features New Releases, never an adult title.
- **Rating ceilings** (`services/content_policy.py`): a user's limits (`rating_ceiling_movie`, `rating_ceiling_tv`,
  `allow_unrated`, from the role or per-user overrides) run the content-policy engine for that user's requests, whatever
  `CONTENT_POLICY_ENABLED` says; that flag only keeps the older owner-PIN regime. Ratings are US certifications from
  TMDB. A background job (`services/classifier.py`, every 2 minutes, 60 titles a run, no-op without a TMDB key) keeps
  them fresh for every title with a TMDB id; `POST /api/v1/settings/classification/run` runs one batch on demand.
  Playback follows the same question (`ContentPolicy.applies_to`): for a rating-limited user live is always served as
  server HLS with every segment checked against the guide, files and provider archives go through the guarded proxy
  (never `direct`), rewind and catch-up intervals are checked whole, and segments and bound artwork are sent
  `private, no-store`. Changing a user's limits moves their version, so streams already running stop within seconds.
- **Recordings** (`services/recorder.py`, `services/recording_policy.py`): `recordings.view` lists and plays
  (`rec:<id>` through `/playback/resolve`), `recordings.schedule` schedules and cancels, and a holder of `users.manage`
  sees and manages everyone's (`system.manage` alone does not). A recording keeps the rating evidence its programme
  had when it was scheduled (`recording.age_evidence`, revision 0012), and a rating-limited user sees, plays and
  schedules only what their ceiling admits; recordings from before that revision have no evidence and count as
  unrated. The padding recorded around a programme is not judged.
- **Guide facts** (`services/sports.py`): every programme payload carries `is_sport` (the classifier behind `/sports`
  and the recorder) and `teams`, the two sides of a title that spells out a matchup (`A vs B`, `A at B`), else `[]`.
- **Watch rooms** (`services/rooms.py`, `room_hub.py`, `room_playback.py`): `POST /rooms/{code}/join` adds a member on
  every call unless the body carries the caller's own `member_token`, which keeps that seat.
  `GET /rooms/{code}/descriptor?member_token=…` (no bearer, authorized like the socket) returns the member's
  descriptor as it is now: on a shared stream the playlist URL they already hold with the current `stream_epoch`,
  otherwise a freshly resolved one. A member always starts at the room's position (`resume_position_ms`). After a
  seek the `state` message is all a client needs: a changed `stream_epoch` means reload the same playlist with
  `stream_start_ms` as time zero; no `stream_epoch` (each member plays their own file) means just seek.
- **Shared devices** (`services/sessions.py`): `POST /auth/switch` to the current user is a no-op that returns fresh
  tokens; sent with a PIN it verifies that PIN (401 `pin_invalid`, same five-guess budget), which is how a client
  asks "is this their PIN?". An expired or stale session answers `token_expired` / `session_stale` before any PIN is
  looked at. Every 429 carries `Retry-After` in whole seconds.
- **Remote access** (`services/network.py`): a user without `remote.access` is served only from the `home_networks`
  setting (private, loopback, link-local and `100.64.0.0/10` ranges by default); elsewhere every call but
  `GET /auth/me` answers 403 `remote_access_denied`. The client address is the connection's peer, or the
  `X-Forwarded-For` hop a proxy listed in `FORWARDED_ALLOW_IPS` appended. The signed media URLs follow the user
  they were issued to: `/stream/{token}…` checks the grant's user (from the validation the grant guard already
  caches, so no lookup per segment), and artwork URLs signed for a user without `remote.access` are marked home-only
  and never publicly cacheable. See "Deploying behind a reverse proxy or tunnel" below.
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

## Requests and their progress

A title no source has can be requested. StreamHub owns the request and its approval (`state`: `pending_approval` →
`approved` → `sent` → `available`, or `declined` / `failed`); a request manager (Overseerr, Jellyseerr or Seerr, set
under Settings) does the fetching. `state` says where the request stands in that flow. What is happening to the title
is `progress.phase`, and it only ever says what an answer of the request manager showed
(`app/services/request_progress.py` has the rules, `app/adapters/seerr/client.py` reads the answer):

| Phase | Means | Comes from |
|---|---|---|
| `requested` | Waiting for approval here. | `state` `pending_approval`; the request manager has not been told. |
| `approved` | Approved here, not taken by the request manager yet. | `state` `approved` (the hand-over failed and is retried; `detail` says why), or the manager's request `status` 1: it holds the request for an approval of its own. |
| `searching` | The request manager has it; nothing is downloading. | Request `status` 2 with media `status` 1–4 or no media record, and an empty or absent `downloadStatus`. Also what a request says between being sent and first being asked about. |
| `downloading` | Its download queue has an unfinished item for the title. | At least one object in the media's `downloadStatus` (`downloadStatus4k` for a 4K request). `percent` is bytes done over bytes total (`size`, `sizeLeft`) across all items, rounded down, and null when any item has no size. `eta` is the latest `estimatedCompletionTime` (else now + `timeLeft`) of the unfinished items, and null unless each gives a time still ahead. `detail`: `Queued` / `Paused` when every unfinished item is waiting, `2 of 8 episodes` for a series (of the episodes in the queue), `1 of 3 downloads has a problem`. |
| `importing` | Downloaded, not in the library yet. | Every queue item is `completed` or has `sizeLeft` 0, and the media is not available. |
| `partially_available` | Series: some of what was asked for can be watched. | Some, not all, of the requested seasons have media season `status` 5 (or the one requested season has 4); with no seasons to count, media `status` 4. While more is downloading the phase is `downloading` and `detail` keeps the count. |
| `available` | Done. | Media `status` 5, request `status` 5, or every requested season at 5; `state` becomes `available` and the requester gets a notice. Also set, without asking the manager, when a catalog sync finds the title in an in-app source. |
| `declined` | Refused. | Declined here (`detail` is the decider's note), or request `status` 3. |
| `failed` | The request manager gave up, or no longer has the request. | Request `status` 4, or `404` for the request. |
| `stalled` | A download is not getting anywhere. | Every unfinished queue item reports `warning`, `failed`, `error`, `stalled` or `downloadClientUnavailable`; or a download that says it is running has not gained a byte for 30 minutes; or finished downloads have not been imported for 30 minutes. Queued and paused items never count as stalled, and neither does a search: a title may rightly be searched for until it is released. |
| `unknown` | Not known right now. | The request manager did not answer (unreachable, timeout, 5xx, rejected key, unreadable body), none is configured, or its answer had nothing these rules know. `last_phase` keeps the phase before. |

A field that is missing or malformed is read as absent, never as progress: a request is `downloading` only while a queue
item exists, and the first answer for a new request is `requested`, `approved` or `searching`. `detail` is built here or
is the manager's own error message, which is dropped when it names a host, an address or a path; a queue item's title
(a release name) is never read. A request the manager already had and gave no id for is followed through the
`mediaInfo` of `/movie/{tmdbId}` or `/tv/{tmdbId}` instead; with no request to read, media `status` 2 is `approved` and
3 is `searching`.

**How often the request manager is asked.** One request at a time is one `GET /api/v1/request/{id}`:

- every 15 s while it is `downloading` or `importing`; every 3 minutes otherwise, and every 30 s during a request's
  first 10 minutes with the manager, so the start of a download is noticed. A job runs every 15 s and asks only about
  the requests that are due, at most 20 a pass. `requested` and `approved` requests are not polled: they change when
  somebody acts on them (an approved request that did not reach the manager is sent again every 5 minutes);
- every 5 minutes, and after each catalog sync, a sweep asks about every sent request and closes those whose title an
  in-app source now has;
- `GET /requests` and `GET /requests/{id}` serve what is stored and first refresh the stale requests among those they
  return: at most 8 per call, waiting at most 4 s. Tiles (`Tile.request_progress`) only ever serve what is stored;
- `POST /requests/{id}/refresh` asks about one request now (at most every 5 s), or sends an unsent one again;
- never more than 4 calls at once. A request the manager fails to answer for is asked about again after 30 s, then 60,
  120, 240 and from then on every 5 minutes. When the manager cannot be reached at all, the first failure costs one
  timeout, the sent requests become `unknown`, and the job and readers ask nothing until that same back-off has passed.

A request closes as `available` when the manager says so, or when a catalog sync finds the title in an in-app source,
whichever comes first. For a series the second usually wins: the first imported episode puts the show in the library.

## Deploying behind a reverse proxy or tunnel

`remote.access` is only enforceable when the server can tell where a request really comes from. Behind a reverse
proxy or a tunnel connector, every connection reaches the server from the proxy's own address, which is usually a
private one, so without further configuration every remote client looks like a device at home and the rule never
refuses anyone.

- List the proxy's address (or its network) in `FORWARDED_ALLOW_IPS`, for example `FORWARDED_ALLOW_IPS=172.18.0.0/16`
  for a proxy on a container network or `127.0.0.1,192.168.1.2` for one on the LAN. Only then is its
  `X-Forwarded-For` read, and only for the hop it appended; a header written by anyone else is ignored.
- Make sure the proxy appends the client address to `X-Forwarded-For` (most do by default) and does not pass a
  client-supplied value through as the last hop.
- Do not set `FORWARDED_ALLOW_IPS=*` on a server reachable without the proxy: every client could then name its own
  address.
- If the tunnel connector runs on the same host, its address is typically loopback or the container gateway; list
  that address, not the tunnel's public one.
- `home_networks` (Settings) says which client addresses count as home. An overlay network's range is in the
  defaults (`100.64.0.0/10`); remove it there if devices on the overlay should count as remote.

To check: sign in as a user without `remote.access` from outside; every call but `GET /auth/me` should answer 403
`remote_access_denied`, and that user's row in the session list should show the outside address.

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
