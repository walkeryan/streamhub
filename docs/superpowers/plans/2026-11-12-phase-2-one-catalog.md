# Phase 2: One Catalog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** StreamHub reads its IPTV catalog from an Xtream-compatible IPTV manager (not the provider), shows it under clean shelves and titles, merges it with the Plex library, and plays any of it through its own proxy on every client.

**Architecture:** The Xtream adapter stays protocol-generic; a `manager` flavor fixes the assumptions that only hold for real providers. Presentation data (shelf, clean title, quality, language, adult flag) is computed once at sync and stored on the `Title`/`Channel` rows, so browse queries stay simple. Clients never receive a source URL: playback and artwork for proxied sources go through StreamHub.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, SQLite, respx/pytest, ffmpeg/ffprobe, React + TanStack Query.

## Global Constraints

- Tests first for every behavior change; follow the patterns in `hub-server/tests/test_xtream.py` (respx) and `tests/test_canonical_browse.py` (direct rows).
- `ruff check`, `ruff format --check`, `mypy app` and `pytest` must pass (`uv run --python 3.12 --extra dev …` in `hub-server/`).
- No provider names, LAN addresses, credentials or personal names in code, tests or docs; use `example.test` hosts and invented category names that mirror the real shapes.
- Existing behavior for real Xtream providers must not regress except where a task says so.
- Schema changes go through an Alembic revision and keep existing databases upgradable.

## Findings that drive the tasks (dev instance against the real manager, 2026-11-12)

1. The manager numbers movies and series independently. Rows are unique on `(source_id, ext_id)` and the adapter uses bare ids, so 6,207 series overwrote the movies with the same number. 29,168 movies were listed; 22,961 survived.
2. Titles carry provider prefixes (`NF - Millionaire`, `4K-A+ - Berlin ER (2025) (DE)`), and categories are raw (`NETFLIX MOVIES ⁴ᴷ ³⁸⁴⁰ᴾ ᴰᵒˡᵇʸ ⱽᶦˢᶦᵒⁿ`, `EN - NEW RELEASE`, `US| SPORT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ`).
3. Language filtering infers language from the category name, so brand categories and Plex sections resolve to `und` and disappear for profiles with a language set.
4. Artwork URLs point at the manager's own host. Remote clients cannot load them.
5. A lazily discovered TMDB id is overwritten with NULL by the next sync, flipping the title's id and orphaning favorites and progress.
6. The manager returns one stream per TMDB id and no audio-track metadata (`audio: {}`), so audio choice must come from probing the file.
7. Proxy passthrough of a VOD file works (`proxy: always`), but a browser cannot play MKV/HEVC/AC-3 directly.

---

### Task 1: Kind-namespaced Xtream ids

**Files:** `hub-server/app/adapters/xtream/provider.py`, `hub-server/tests/test_xtream.py`.

Series, seasons and episodes get namespaced `ext_id`s (`s:<series_id>`, `s:<series_id>:s<n>`, `e:<episode_id>`); movies and channels keep bare ids. `stream_for_item`, `children`, `detail` strip the prefix before calling the panel.

- [ ] Test: a panel whose movie `7` and series `7` both exist yields two rows after sync; the series detail and an episode stream URL use the bare ids.
- [ ] Implement; run the Xtream tests.

### Task 2: Presentation fields computed at sync

**Files:** create `hub-server/app/adapters/xtream/presentation.py`; modify `provider.py`, `hub-server/app/infra/models.py`, new Alembic revision, `hub-server/app/services/sources.py` (upsert), `tests/test_xtream_presentation.py`.

**Produces:**
```python
@dataclass(frozen=True)
class Presented:
    title: str            # "The Mill"
    year: int | None      # 2023
    shelf: str            # "New Releases"
    quality: str | None   # "4K" | None
    language: str         # ISO 639-1/2 code or "und"
    kids: bool
    adult: bool
    raw_category: str

def present_title(name: str, raw_category: str, *, year: int | None, is_adult: bool,
                  default_language: str | None, shelf_map: Mapping[str, str]) -> Presented
def present_group(raw_group: str, *, group_map: Mapping[str, str]) -> str | None   # None = hidden
```
New `Title` columns: `language` (indexed, default `und`), `quality` (nullable), `adult` (bool, indexed, default false). `group_name` holds the shelf; the raw category goes to `extra["raw_category"]`.

Rules (generic; deployment specifics come from source config `shelf_map`, `group_map`, `default_language`, `hidden_groups`):
- Title: drop a leading tag made of 2–6 letters/digits/`+` optionally preceded by `4K-` and followed by ` - ` (`EN - `, `NF - `, `4K-A+ - `, `EN-TOP - `); drop a trailing `(YYYY)` into `year` when the row has none; drop a trailing two-letter country tag `(US)`; collapse whitespace. A leading `4K-` sets quality `4K`.
- Shelf: strip a leading language tag (`EN - `, `ENGLISH `); strip quality noise (superscript runs, `3840P`, `4K`, `HEVC`, `DOLBY AUDIO`, `DOLBY VISION`, `BLURAY`, `(MULTI)`, `(MULTI-SUBS)`) and set quality `4K` when a 4K marker was present; map streaming brands to their names (`NETFLIX…`→`Netflix`, `AMAZON…`→`Prime Video`, `APPLE+…`→`Apple TV+`, `DISNEY+…`→`Disney+`, `HBO MAX…`→`Max`, `PARAMOUNT+…`→`Paramount+`, `PEACOCK`, `SHOWTIME`, `SKY`, `DISCOVERY+…`, `CRUNCHYROLL…`); `KIDS` anywhere sets `kids`; otherwise title-case the remainder with small fixes (`NEW RELEASE`→`New Releases`, `SCIENCE FICTION`→`Science Fiction`, `MANGA/ANIME`→`Anime`, `DOCU-…`/`DOCUMENTARIES`→`Documentaries`). `shelf_map` (exact raw name → shelf) wins over every rule.
- Language: explicit language tag in the raw category or title prefix, else `default_language`, else the existing inference, else `und`.
- Adult: the panel's `is_adult`, or a raw category in config `adult_categories`.
- Channel groups: strip a leading `XX| ` country tag and quality noise, title-case; `group_map` overrides; names in `hidden_groups` return `None` and their channels are not stored.

- [ ] Table-driven tests for every rule above with invented names of the real shapes.
- [ ] Implement `presentation.py`; wire it into movie, series and channel sync; migration; upsert writes the new columns.
- [ ] Language filter (`CatalogService._filter_title_languages`) uses `Title.language` instead of re-deriving from `group_name`; Plex and Jellyfin rows get `default_language` from their source config (default `und`, so behavior is unchanged until configured).
- [ ] Test: with profile languages `["en"]`, a brand-category title and a Plex title with source `default_language="en"` are both listed.

### Task 3: Sync never erases identifiers

**Files:** `hub-server/app/services/sources.py`, test alongside existing sync tests.

- [ ] Test: a row with `tmdb_id` set, re-synced with an item lacking it, keeps the id (and `imdb_id`).
- [ ] Implement with `COALESCE(excluded.tmdb_id, title.tmdb_id)` in the upsert.

### Task 4: Artwork for proxied sources

**Files:** `hub-server/app/services/tiles.py` (and where posters/backdrops/logos are emitted), `services/image_grants.py`, `api/routers/playback.py`, tests.

- [ ] Test: for a source with `proxy: "always"`, tile and detail artwork URLs are `/api/v1/images/<token>` and fetching one returns the upstream bytes with a cache header; for other sources URLs are unchanged.
- [ ] Implement, reusing the existing image-grant route; cache responses on disk under `/config/cache/images` with a size cap.

### Task 5: Adult rows stay out of general surfaces

**Files:** `services/home.py`, `services/catalog.py`, `services/recommendations.py`, tests.

Until Phase 4 adds permissions, rows with `adult=true` are excluded from home rows, hero, recently added, search and browse unless the request passes `include_adult=true` and the profile is the admin.

- [ ] Tests for each surface; implement one shared query predicate.

### Task 6: Web playback of any VOD file

**Files:** `services/playback.py`, `services/audio_selection.py`, `proxy/stream_proxy.py`, `api/routers/playback.py`, web `player/usePlayback.ts`, `screens/Title.tsx`, tests.

- [ ] Probe on demand: `GET /playback/options` for a title whose source gives no audio metadata runs `ffprobe` against the source URL (10 s cap), caches streams in `Title.extra["probe"]` (video codec, resolution, audio tracks with language, codec, channels), and returns audio options from it.
- [ ] `decide_mode` for `client=web` VOD: direct/proxy only when the probe says the browser can decode it (H.264 + AAC/MP3 in MP4); otherwise HLS, copying video when it is H.264 and transcoding it otherwise, always encoding the chosen audio track to AAC stereo. Seeking works (`-ss` restart or indexed segments).
- [ ] The resolve request accepts `audio_track` (index from the probe); HLS sessions key on it.
- [ ] Transcode honours the `transcode_hw` setting without requiring the per-source `transcode` flag when the codec demands it; software fallback when no hardware is configured.
- [ ] Tests with ffmpeg stubbed (argument assertions) plus one real-ffmpeg test on a generated 2-second sample, skipped when ffmpeg is absent.

### Task 7: "Streams busy" says who

**Files:** `proxy/stream_proxy.py`, `api/routers/playback.py`, web player error view, tests.

- [ ] `source_busy` responses include `active: [{profile_name, title, kind, since}]` from live grants; an upstream 4xx/5xx from the manager on stream open maps to `source_busy` when its limit is the cause.
- [ ] Web shows "Both streams are in use: <who> is watching <what>".

### Task 8: Plex source and merge check

- [ ] Owner links Plex with the PIN flow on the dev instance (owner action).
- [ ] Verify: a title in both sources shows once, owned copy first; set Plex `default_language`.
- [ ] Two-way Plex progress moves to Phase 4, where users can be linked to Plex home users.

### Task 9: Verify on the dev instance

- [ ] Re-sync; movie count equals the manager's list; search "The Mill" finds the 2023 film as "The Mill"; shelves are clean; artwork loads through `/api/v1/images/`; the film plays in Chrome with an audio-track choice.
