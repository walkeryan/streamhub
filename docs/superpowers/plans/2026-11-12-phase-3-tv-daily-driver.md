# Phase 3: TV App as a Daily Driver — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every screen of the Android TV app is fully usable with a four-way remote plus Select, Back and Home, playback failures are recoverable from the couch, and the catalog is browsable by shelf.

**Architecture:** No new modules. Fixes land where the behavior lives (`app`, `core/data`, `core/player`, `feature/*`). Logic that can be unit-tested is pulled out of composables into plain Kotlin (key maps, error mapping, back policy) and tested on the JVM.

**Tech Stack:** Kotlin 2.3, Compose for TV, Media3 1.11, Hilt, Ktor, DataStore. AGP 8.13.2, compileSdk 36.

## Global Constraints

- Remote-only: every interactive element reachable and operable with D-pad, Select, Back. No dead ends. Back always restores focus to where the user came from.
- Safe area 48 dp horizontal / 27 dp vertical for anything interactive.
- DTOs are hand-written in `core/data/.../model/Dto.kt` and must tolerate unknown and missing fields.
- No server changes in this phase; where a server feature is still to come the client codes to the contract given here and degrades gracefully when the endpoint is absent (404).
- JVM unit tests for all extracted logic; the CI Gradle command must run every module that has tests.
- No personal data, hostnames or credentials in code or tests.

## Starting point

A read-only survey on 2026-11-12 found that five items from `docs/UAT-REPORT.md` are already fixed (rail safe area, player menu without a Menu key, focus restore on Home and Search, adaptive accent, first-run disclaimer). The tasks below are what remains, plus defects the review did not cover.

---

### Task 1: Keep the session through server restarts
`core/data/.../api/HttpClientFactory.kt` clears the session on any non-2xx from `/auth/refresh`. Clear only on 400/401/403; treat everything else (and I/O errors) as transient and keep the tokens.
- [ ] Test the decision as a pure function (`shouldClearSession(status: Int?): Boolean`), then wire it.

### Task 2: Back policy
`app/.../StreamHubRoot.kt`, `core/ui/.../components/LeftRail.kt`. Today Back on a top-level screen toggles rail and content forever, and Back on Home exits at once.
- [ ] Extract `BackPolicy.decide(route, isTopLevel, railExpanded, lastBackAtMs, nowMs): BackAction` with actions `OpenRail`, `GoHome`, `ConfirmExit`, `Exit`, `Default`; unit-test: top-level non-Home with content focused → `OpenRail`; rail open on non-Home → `GoHome`; Home → `ConfirmExit` then `Exit` within 2 s.
- [ ] Wire it; show "Press Back again to exit" on Home.

### Task 3: Friendly, focusable errors
New `core/data/.../api/ApiErrors.kt`; `PlaybackCoordinator.kt`, `StreamHubRoot.kt`.
- [ ] `ApiErrors.describe(e): UserError(title, body, canRetry)` mapping `source_busy` (use the optional `active: [{profile_name, title}]` list when present: "Both streams are in use. <name> is watching <title>."), `source_limit_reached`, `audio_selection_required`, `disclaimer_required`, 401/403, 404, 5xx and network failures. Unit-test each.
- [ ] Replace the Toast with a dialog whose default focus is Try again (when `canRetry`) or OK.

### Task 4: Player you can drive
`feature/player/PlayerScreen.kt`, `PlayerViewModel.kt`, `core/player/StreamHubPlayer.kt`.
- [ ] VOD control row, shown on Select or Down and hidden after 5 s idle: scrubber (Left/Right seek 10 s, hold accelerates), Play/Pause, −10, +10, Start over, Audio & subtitles. Select on the scrubber toggles play/pause.
- [ ] Audio and subtitle menus list every track; tracks the device cannot decode are shown disabled with "(not supported on this TV)" instead of being hidden.
- [ ] Error overlay with focused Try again / Back; Try again re-resolves through `PlaybackCoordinator` rather than replaying the stale descriptor.
- [ ] On exit call `stop()` before `release()` so the final position is reported.
- [ ] `BACK_DOUBLE_MS` 750; show "Back again for last channel" once after the first Back in live playback.
- [ ] Extract the key map (`PlayerKeyMap.resolve(key, isLive, menuOpen, controlsVisible, longPress): PlayerCommand`) and unit-test it.

### Task 5: Category shelves
`core/data/.../api/StreamHubApi.kt`, `model/Dto.kt`, `CatalogFilters.kt`, `feature/home/.../BrowseScreen.kt` (+ view model).
- [ ] API: `browseCategories(section, sourceId, language, q)` → `GET /browse/{section}/categories`; add `category` and `q` to `browse()` and `browseGenres()`; `BrowsePage.hasMore`, `pageSize`; `NativeBrowseFilters.category`. Contract tests against `contracts/openapi.yaml` shapes.
- [ ] Browse opens as shelves: a `LazyColumn` of rows, one per category in server order, each row loading its first page lazily when it scrolls into view, ending in a "See all" tile that opens the existing grid filtered to that category. The filter buttons stay available above the shelves.
- [ ] Focus: Down/Up between shelves keeps the column position where possible; Back from a title returns to the same tile; closing a filter picker returns focus to the button that opened it; tiles are kept while the next page loads.

### Task 6: Live TV and guide polish
- [ ] Number keys in the channel list and the guide jump to the channel with a 2 s commit, reusing the player's digit logic (extract it).
- [ ] Select on a future guide programme shows "Starts at HH:mm".
- [ ] No automatic live preview: opening a stream while browsing would consume one of the source's few connections.

### Task 7: Smaller fixes
- [ ] Settings uses `InitialFocusOnce`. Search keeps its query in `SavedStateHandle` and does not auto-focus the text field on entry (first focus goes to results when present).
- [ ] `design/tokens.json` rail widths match `RailMetrics`.

### Task 8: Update check
Contract (server side lands in a later phase): `GET /api/v1/app/tv` → `{"version_code": int, "version_name": str, "apk_url": str, "sha256": str}`; 404 means no update service.
- [ ] `core/data` call + `UpdateRepository.check(): UpdateInfo?` (null on 404 or when `version_code <= BuildConfig.VERSION_CODE`); unit-test.
- [ ] Settings → About shows "Update available: <name>" with Install, which downloads to app-private storage, verifies the SHA-256, and launches the package installer (`REQUEST_INSTALL_PACKAGES`, FileProvider).

### Task 9: Optional software audio decoding
- [ ] Gradle property `streamhub.ffmpegDecoder=true` adds a prebuilt Media3 FFmpeg decoder dependency matching the Media3 version and prefers extension renderers; default off so the default build keeps its license. Document it in `tv-app/README.md`. Skip with a note if no artifact matches the pinned Media3 version.

### Task 10: CI covers what exists
- [ ] `.github/workflows/ci.yml` Android job runs unit tests for every module that has them plus `:core:ui:checkTokens`, then `:app:assembleDebug`.

### Verification
- [ ] CI Android job green.
- [ ] On the real TV over ADB: a scripted remote-only walk of every screen with screenshots, and a two-hour soak each of a film, an owned-library film and a live channel (needs a signed build; tracked with the Phase 3 device work).

Deferred to Phase 4: the "Who's watching?" picker (needs the server's user model).
