# StreamHub UAT and design review

**Date:** 2026-10-31 · **Reviewer:** independent UAT agent (did not build the product) · **Rubric:** `design/BRIEF.md` UAT checklist (items 1–10)

**Builds reviewed**
- Web: `web-app/dist` served by hub-server on :8787, seeded demo IPTV source (6 channels, 270 programmes). Driven **keyboard-only** (Arrows, Enter, Escape/Backspace, digits) in Google Chrome via Playwright at 1920×1080, 1280×720 and 390×844 (phone, touch). The disclaimer gate was tested on a throwaway second backend (:8788, fresh config) so the shared instance's acceptance stayed untouched.
- Android TV: static review of `tv-app` at `48a0850` (`feature/*`, `core/ui`, `core/data`, `app`).

**Screenshots:** `/tmp/claude-0/-home-claude/f39cd544-9a79-52ed-9cec-ac1ee8591031/scratchpad/uat/*.png` (86 files; names are cited below; not committed).

**Environment limits.** Third-party artwork from `upload.wikimedia.org` was blocked in the sandbox browser (`ERR_BLOCKED_BY_ORB`), so the hero backdrop and Poster Adaptive tinting could not be checked visually. The demo data has no VOD, so Title detail, Continue Watching, the resume label and hand-off were reviewed in code only.

---

## Scorecard

| # | Criterion | Web | Android (static) | Verdict |
|---|---|---|---|---|
| 1 | Focus visible from 3 m, ring ≥3:1, never colour-only | Pass | Pass | **Pass** (after fix ca4b98b) |
| 2 | Text contrast ≥4.5 / ≥3 large; body ≥16 dp | Partial | Pass (tokens) | **Partial** |
| 3 | Nothing interactive outside 48/27 dp safe area | Fail (rail) | Fail (rail) | **Fail** |
| 4 | D-pad + Back + OK only; no dead ends; Back restores focus | Partial | Partial | **Partial** |
| 5 | Hero and rows match brief; no promotions; your sources first | Partial | Pass | **Partial** |
| 6 | Play on in-app starts stream directly; hand-off labelled "Open in app" | Pass | Pass | **Pass** |
| 7 | Live theme switch; High Contrast AA; reduced motion | Pass | Partial (adaptive inert) | **Pass** |
| 8 | Live TV: zap OSD, number entry, favorites filter, language filter | Pass (features); P0 on proxied streams | Pass | **Partial** (P0-1) |
| 9 | Disclaimer gate blocks IPTV/M3U; TMDB + JustWatch attribution | Partial | Partial | **Partial** |
| 10 | Motion 120–260 ms ease-out; no layout shift on focus | Pass | Pass | **Pass** |

**Totals:** 4 pass · 5 partial · 1 fail. Blockers: **1 P0, 7 P1**.

### Evidence per item

**1. Focus — Pass.** Every control gets a 3 dp ring (4 px at 1080p; 4 dp in High Contrast), a glow and scale 1.08 (1.12 in High Contrast, 1.02 for dense lists). Ring against background is ≥14.9:1 in every theme (table below). Focus is never colour-only. Android `FocusSurface` uses the same ring + scale + glow (`graphicsLayer`, so no layout shift). One defect found and fixed: in the Guide, Left moved focus onto programme cells hidden under the sticky channel column (`visibleAtCentre=false` four presses in a row; `43-guide-left-*.png`). After the fix (`44-guide-left-fixed.png`) focus stays visible. Screens: `10-home-1920`, `20-livetv-1920`, `40-guide-1920`, `74-settings-theme-contrast`.

**2. Contrast and type — Partial.** Core tokens pass everywhere: text/bg 16.6–21:1, muted/bg 7.1–16.8:1, accent text on accent 5.2–14.9:1. Failures are all secondary text: muted text on the "now" EPG cell (midnight 3.97:1) and on past EPG cells (midnight 3.97, daylight 3.39). Badges use 11 dp text, below the 13 dp label size. Body is 1 rem = 24 px at 1080p (≥22 px target). Poster Adaptive's accent text could fall to 2.33:1; fixed in ede799a (worst case now 4.58:1).

**3. Safe area — Fail.** Measured from bounding boxes at 1920×1080: every rail item sits at x=12 px, and the first starts at y=24 px (`Home [12,24,95,90]`), on every screen (`10-home-1920`, `12-rail-expanded`). Android `LeftRail.kt:79` uses `horizontal = 12.dp`. Content areas pass: they are offset by rail + 48 dp, and the off-screen tiles that peek in are scroll content. Focus-driven scrolling used to leave the focused row flush with the bottom edge (gap −1 px, `29-livetv-last-row`); fixed in ca4b98b (gap now 51 px, `29b-livetv-last-row-fixed`). Backgrounds bleed to the edges correctly.

**4. D-pad operability — Partial.**
- Works on web: Home → row → channel → player → Back restores the tile (`restored=true`). Live TV row → full screen → Back restores the row, including after a number jump and after the error screen's Back button. Rail opens with Left from the first item and closes with Back. Modals trap Back and restore focus. Every Settings tab is reachable.
- Broken on web: Search loses the query and focus on Back (P1-4, `51-search-after-back`). The live player's control bar cannot be reached with the D-pad (P1-2). The About legal text cannot be scrolled with the D-pad (P2).
- Android (static): `focusRestorer()` is on every `LazyRow`/`LazyColumn`/`LazyVerticalGrid` and on the rail. No `TvLazy*` is used. However, `LaunchedEffect(Unit)` focus requests on Home and Search override restoration when returning (P1-5). The player menu needs a Menu key that most Google TV remotes lack (P1-3).

**5. Hero and rows — Partial.** Hero + On Now row render, with the "Play · Demo IPTV" source chip, no promotions, and 8 s auto-advance paused while a hero button is focused (verified over 8.8 s). Focusing a tile drives the hero (title changed to "Demo Sport"). Two misses: with no artwork the hero is a ~600 px empty band, and a failed logo shows the browser's broken-image glyph in the hero, tiles, Live TV and Guide (P1-7; `10-home-1920`, `20-livetv-1920`). Continue Watching, Recently Added and Watchlist could not be exercised (no VOD).

**6. Play directly — Pass.** Web: hero Play / channel tile / channel row → `/play/:id` → `POST /playback/resolve` → `<video>`, with no intermediate screen. Android: `PlaybackCoordinator.play()` resolves, then emits `StartPlayer` or `Handoff` (`PlaybackCoordinator.kt:45-63`). Hand-off is labelled "Open in app" (`Home.tsx:68`, `Title.tsx:343`) and the source chips read "Open · Netflix". Hand-off itself was not exercised live (no hand-off titles in demo data).

**7. Themes — Pass.** All 5 themes were switched by keyboard in Settings → Appearance. Each applied instantly with no reload and was persisted to the profile (`Admin:light` via API) (`74-settings-theme-*`, `75-{home,live,guide}-theme-*`). High Contrast tokens are ≥6:1 for every text pair. With `prefers-reduced-motion`, the focus transform is `none` and transitions are 1 µs. Android Poster Adaptive is never wired up (P2).

**8. Live TV — Partial.** Language chips filter correctly (All 6 / English 2 / Spanish 1 / French 1 / German 1 / Arabic 1, `28-livetv-lang-arabic`). Favorites: Add favorite → heart icon, then "Favorites only" shows exactly that channel, and it appears on Home and Favorites (`26`, `27`, `82`). Number entry: "4" shows an overlay and focus jumps after 2 s. In the player: OSD on zap, hidden after 4 s idle; Up/Down zap with wrap; digit "3" commits to Démo Cinéma after 2 s; Backspace toggles last channel; OK opens the mini list focused on the current channel (`30`–`35`). Preview tunes after a 600 ms dwell. **But** Demo Sport, the one channel routed through the server proxy, never plays: every segment returns 500 (P0-1, `36-player-proxy-channel-error`). Android code matches the brief: OSD 4 s, number commit 2 s, Back twice within 500 ms → last channel, long-press OK to favourite, language and favourites chips.

**9. Legal — Partial.**
- Gate works: Accept is locked for **2997 ms** (counter 3→2→1), Enter during the lock does nothing, default focus is Accept, OK after unlock enters the app without pressing an arrow first, and Exit shows a "You declined" state with a way back (`02`, `03`, `05`). Server enforcement exists (`sources.py:154`, plus non-admins get 403).
- JustWatch attribution is visible under search results and in About. TMDB notice text is visible in About (`73-settings-about`).
- Gaps: no TMDB/JustWatch **logos** (P1-6). The Add M3U/Xtream dialog does not repeat the notice. Android shows the gate only when adding an IPTV source, not on first run (P2).

**10. Motion — Pass.** Tokens are 120/180/260 ms `ease-out`, hero crossfade 420 ms (per brief), all honoured in CSS and Compose (`EaseOutCss`). Sibling tile offsets were identical before and after focus moves (scale is a transform, `layout offsets stable = true`).

### Contrast table (computed from `design/tokens.json`)

| Pair | Midnight | OLED | Daylight | High Contrast |
|---|---|---|---|---|
| text / bg | 17.54 | 21.00 | 16.55 | 21.00 |
| muted / surface_alt (chips) | 6.31 | 6.79 | 6.61 | 15.13 |
| accent_text / accent (primary button) | 8.89 | 8.00 | 5.17 | 14.87 |
| focus_ring / bg | 19.18 | 21.00 | 16.55 | 14.87 |
| danger / bg | 4.90 | 6.94 | 4.51 | 6.06 |
| muted on EPG "now" cell | **3.97** | 4.52 | 4.78 | 8.29 |
| muted on past EPG cell (opacity 0.72) | **3.97** | **4.08** | **3.39** | 8.04 |
| focus_ring / accent (ring on primary button, 3 px gap) | 2.16 | 2.63 | 3.43 | **1.00** |

### Console and network
- No page errors and no unexpected 4xx.
- 24 × `500 GET /api/v1/stream/<token>/seg/…` (P0-1).
- `ERR_BLOCKED_BY_ORB` on wikimedia logos (environment; see P2-12).
- One warning on Exit: "Scripts may close only the windows that were opened by them" (`Gate.tsx:147`, harmless).
- No horizontal overflow at 1280 or 390 on any screen.

---

## Defects

### P0 — blocks use

**P0-1 Every proxied stream fails (HTTP 500 on segments).**
- Cause: `hub-server/app/proxy/stream_proxy.py:314` calls `self._client_factory(timeout=httpx.Timeout(30.0, read=None), …)`. `make_client` (`app/infra/http.py:21-23`) then builds `httpx.Timeout(timeout, connect=10.0)`, and httpx raises `AssertionError` when given a `Timeout` instance plus another argument (reproduced in the server venv). `stream_segment` (`app/api/routers/playback.py:128`) only catches `ConnectionError`/`OSError`, so the error becomes a 500.
- Impact: every source the resolver sends through `proxy` fails. That includes `#EXTVLCOPT` User-Agent/Referer channels (very common with IPTV providers), http streams on an https site, CORS-blocked hosts, and `max_connections=1`. Playlists still rewrite fine, so the player looks like it starts, then dies with `fragLoadError` after 6 retries (`36-player-proxy-channel-error`). The same `passthrough()` serves VOD passthrough too.
- Fix:
  - In `make_client`, use `timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout, connect=10.0)`, or pass `httpx.Timeout(30.0, connect=10.0, read=None)` from `passthrough`.
  - Add `except Exception → BadGateway` to `stream_segment` to match `stream()`.
  - Add a contract test that GETs a proxied segment (with and without `Range`).

### P1 — must fix before sharing

**P1-1 Left rail violates the safe area on both platforms.**
- Web: `.rail` `padding: 1rem 0.5rem` (`web-app/src/styles/base.css:113`) puts items at x=12 px, y=24 px. Android: `LeftRail.kt:79` uses `horizontal = 12.dp`.
- Fix: keep the rail background full-bleed, but inset the items. Set `padding-inline-start: var(--safe-x)` / `start = SafeMarginX` and `padding-top: var(--safe-y)`. Grow the collapsed width to safe margin + 56 dp (≈104 dp; update `tokens.rail.collapsed_w`) and shift `.main` / content padding to match.

**P1-2 Web live player: control bar unreachable by D-pad.**
- Cause: the capture-phase handler (`web-app/src/screens/Player.tsx:120-160`) consumes every Arrow and Enter while live (Up/Down zap, OK opens the list). Focus never reaches Back, Audio and subtitles, PiP or Full screen (`D-pad focus inside player controls (live): null`). The menu opens only with `m` or ContextMenu, which TV remotes and browsers-on-TV don't send.
- Fix (either option):
  - Make Down with the OSD visible (or long-press OK) do `setOverlay('bar')`, as for VOD, and zap only while the bar is unfocused.
  - Put an "Audio, subtitles and more" row at the top of the OK mini list.

**P1-3 Android player menu needs a Menu key.**
- `PlayerScreen.kt:154` opens audio/subtitles/aspect/sleep only on `Key.Menu`/`Key.Settings`. The Chromecast with Google TV, onn and most current Google TV remotes have no Menu key, so these settings are unreachable for many users.
- Fix: long-press OK opens the menu (`nativeKeyEvent.isLongPress` / repeatCount > 0 on DPAD_CENTER). For VOD, let Down reveal a focusable control row.

**P1-4 Web Search loses the query and focus on Back.**
- `Search.tsx:13` uses `useState('')` and `Search.tsx:21` restores focus immediately. Returning from a result shows an empty field and no results, so the user must retype (`51-search-after-back`).
- Fix: keep `q` in the URL (`?q=`, `replace: true`) or in `useUi`, and call `useRestoreFocus(!q || !!data, 'search:input')` so the saved result key exists before restoring.

**P1-5 Android Back does not restore focus on Home or Search (static; confirm on device).**
- `HomeScreen.kt:74` `LaunchedEffect(Unit) { playFocus.requestFocus() }` and `SearchScreen.kt:56` `LaunchedEffect(Unit) { fieldFocus.requestFocus() }` re-run every time NavHost recomposes the destination after Back. Focus jumps to hero Play or to the text field (which may raise the IME) instead of the tile that opened Details.
- Fix: guard with a `rememberSaveable { mutableStateOf(false) }` "initial focus done" flag, or save the last-focused item key and request that instead.

**P1-6 TMDB and JustWatch logos missing.**
- Web `Settings.tsx:288-289` points at `/attribution/tmdb.svg` and `/attribution/justwatch.svg`, which don't exist, so About falls back to bold text (`73-settings-about`). Android `SettingsScreen.kt:233` is text-only by design.
- `docs/LEGAL.md` requires "TMDB logo + notice, JustWatch logo", and TMDB's API terms require its logo. The engineering rule "never bundle provider logos" is about streaming-service logos.
- Fix: clarify that in LEGAL.md, then ship the two official attribution SVGs in `web-app/public/attribution/` and Android `res/drawable`.

**P1-7 Missing art shows a broken-image glyph and an empty hero.**
- `Home.tsx:62` (hero logo), `Tile.tsx:81` (channel tile logo) and `ui.tsx:165` (`LogoBlock`) render `<img>` with no `onError`. Any 404 or blocked logo shows the browser's broken-image icon in the hero, On Now, Live TV and Guide (`10-home-1920`, `20-livetv-1920`, `40-guide-1920`). IPTV logo URLs fail often.
- With no backdrop the hero is a ~600 px empty band.
- Fix: on `onError`, fall back to the title text or the initials block. Hero fallback: a blurred enlargement of the logo or tile colour block, and shrink the hero height when there is no art.

### P2 — polish

1. **Guide Left path (web).** Left now alternates between programme cells and the sticky channel cell (`Guide.tsx:396`). Make the channel cell the row's left stop: Left from a cell scrolls back in time within the row, and Left from the channel cell opens the rail.
2. **Up from the Live TV list always lands on the language "All" chip**, skipping the group/favourites row (`LiveTV.tsx:99`, FocusGroup remembers its first child). Set `remember={false}` or split the two chip rows into separate groups.
3. **EPG secondary text below 4.5:1** (`base.css:219-220`, table above). Drop the 0.72 opacity on past cells in favour of a muted background, and use `--t-text` for the time label on the "now" cell.
4. **Badges use 11 dp text** (`base.css:158`), below the 13 dp label token. Use `--text-label`.
5. **About legal text can't be scrolled with the D-pad** (no focusable below the tabs; page 1139 px tall). Make the legal panel a focusable scroll region.
6. **The Add M3U/Xtream dialog doesn't repeat the legal notice** as LEGAL.md requires (`Sources.tsx:153`). Add a short notice with a "Read full notice" link.
7. **Phone (390 px):**
   - The preview aside, including **Add favorite**, is hidden (`LiveTV.tsx:129` `max-md:hidden`), so channels can't be favourited on phone.
   - The bottom tab bar clips Settings and Profile, and its items are 45×41 px, under the 44 px minimum (`91-live-phone`).
   - Fix: 5 tabs + More; a heart toggle on each row on phones.
8. **Web Back-twice = last channel is not implemented.** Remote Back (Escape/461/10009) exits immediately; last channel works only with keyboard Backspace (`Player.tsx:145`). Mirror Android: a second Back within 500–800 ms toggles last channel.
9. **Android Poster Adaptive is inert.** `ThemeRepository.setAdaptiveAccent` is never called, and `StreamHubTheme.kt:96` never recomputes `accentText`. When wiring it up, pick the higher-contrast text colour (see web fix ede799a).
10. **Android first-run gate.** The disclaimer appears only when adding an IPTV source (`SourcesScreen.kt:83`), not on first run per LEGAL.md. The text is hardcoded (`Disclaimer.kt:35`) instead of fetched from `GET /disclaimer`, so the version can drift.
11. **Android player details:**
    - The Back-twice window is 500 ms (`PlayerViewModel.kt:216`), tight for Bluetooth/IR remotes. Use 700–800 ms with a "Back again for last channel" hint.
    - OK on a future programme in the Guide silently does nothing (`GuideScreen.kt:156`; web shows "starts at" toast).
    - The mini-list focused row uses ring colour = fill colour, so focus there is effectively fill inversion only.
12. **Channel logos load directly from third-party hosts** (wikimedia in the demo) instead of the server image proxy. ARCHITECTURE says clients never talk to upstreams, and direct loads leak client IPs and risk mixed content. Rewrite logo URLs through `/api/v1/img`.
13. **High Contrast:** the focus ring on a primary button is the same yellow as the button (1:1, separated only by a 3 px gap). Use a white ring on accent fills in that theme.
14. **Hero meta for a channel shows a bare "1".** Prefer "Ch 1 · Now: Deep Dive". The EPG detail line has stray spacing ("11:00 AM   Demo", `Guide.tsx:431`).

### Fixed during this review (web-app only, each its own commit)

| Commit | Fix | Verified |
|---|---|---|
| `ca4b98b` | `scroll-padding` on the document (safe-y) and the EPG scroller (sticky column + header): focused rows no longer sit flush with the bottom edge; Guide focus is never hidden under the channel column | bottom gap −1 → 51 px; Guide `visible=true` on every Left (`29b`, `44`) |
| `ede799a` | Poster Adaptive picks black or white accent text by actual contrast (threshold was 0.4 against a ~0.18 crossover) | worst case 2.33:1 → 4.58:1 across the sRGB grid; `tsc` and `eslint` clean |
| `d086a91` | Live channel OSD sits above the control bar while the chrome is visible (it covered the buttons, `32-player-zap-osd`) | no overlap (`32b`, `32c`); OSD still auto-hides at 4 s |

---

## What's good

- **The TV fundamentals are right on both clients.** Spatial navigation is deliberate: a Back stack, focus memory per history entry, and modal focus traps. Focus styling follows the tokens exactly and never relies on colour alone. Every Android lazy container has `focusRestorer()` and nothing uses the deprecated `TvLazy*`.
- **The Live TV experience compares well with TiviMate.** It has a 600 ms preview dwell, language and group chips with counts, favourites filter, number entry with an on-screen buffer, an OSD with now/next and progress, wrap-around zapping, a mini list opened with OK that is focused on the current channel, and catch-up handling in the Guide with a now-line. Errors recover cleanly: retries with backoff, then a focused "Try again / Back" screen that returns to the right row.
- **The legal gate is implemented properly.** It shows the real 3 s lock, default focus on Accept, a declined state that doesn't dead-end, server-side enforcement, and a versioned notice.
- **Theming is solid.** All five themes come from one token file, switch live with a preview card, persist per profile, and honour reduced motion. High Contrast is well above AA.
- **Responsive without breakage.** There is no horizontal overflow at 1920, 1280 or 390, and the phone layout turns the rail into a bottom bar.
- **The Android player is close to the brief.** It has OSD 4 s, number commit 2 s, Back-twice for last channel, a 350 ms zap settle, Channel Up/Down and Last Channel keys, and a sleep timer.
