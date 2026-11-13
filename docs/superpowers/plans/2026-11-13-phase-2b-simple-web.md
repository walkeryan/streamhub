# Phase 2b: A Simple Web and Phone Interface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The web app opens on content, not controls. A first-time viewer on a phone, tablet or console browser can find and play something without reading a filter bar.

**Architecture:** No new data model. The home feed and browse screens are reorganised around shelves (the category names computed at sync); filters remain but move behind one control. Server changes are limited to the home feed composition and one endpoint that returns shelves with their first items in a single call.

**Tech Stack:** React 19, TanStack Query, Norigin spatial navigation, Tailwind v4 tokens; FastAPI for the two server changes.

## Global Constraints

- Follow `design/BRIEF.md` and `design/tokens.json`; no new colors, fonts or component libraries.
- Everything stays operable by keyboard/D-pad (console browser) and by touch; focus restoration on Back is preserved.
- Phone (≤ 480 px wide): no horizontal page scroll; the first poster row is visible without scrolling on Home and on Movies/Shows.
- Controls that would offer only one choice are not shown (a language chip row when the catalog has one language, a region row when all channels share a region, a source picker with one source).
- Tests: server unit tests for the feed and shelves endpoint; Playwright (mock build) for phone and desktop layouts, keyboard-only and touch.

## Findings (screenshots of the dev instance, 2026-11-13)

1. Home's hero and first row are driven by "recently added", which right after enabling categories is a flood of one niche shelf. There is no row per shelf.
2. Movies/Shows open on a filter wall: on a phone seven controls (Discover/My library, two language chips, another language button, search, category, genre, source, four sort chips) come before the first poster.
3. The rail has unlabeled icons, two of them identical; the phone bottom bar shows nine icons.
4. Home carries a "Download Android APK" button among the content shortcuts.
5. Live TV shows language and region chips even though every channel is the same language and region.

---

### Task 1: Home feed by shelf (server)
`hub-server/app/services/home.py`, tests.
- Row order: Continue Watching (when non-empty) → On Now (channels with a current programme, favourites first) → **New Releases** → up to eight shelf rows chosen by size and recency, alternating movies and shows → Recently Added last. Each row carries `browse_path` for its "See all".
- Hero: up to eight titles from the New Releases shelf (or the largest movie shelf when absent) that have a backdrop and a rating ≥ 6, most recent first. Never adult, never a sports replay shelf.
- Owner-configurable: setting `home_shelves` (ordered list of shelf names) overrides the automatic choice.
- [ ] Tests for ordering, hero filter and the override.

### Task 2: Shelves endpoint (server)
`GET /browse/{section}/shelves?limit_rows=&limit_items=` → `{shelves: [{name, count, items: [tile…], browse_path}]}` in one query round, respecting language, permissions/policy and the adult scope. Contract + tests.

### Task 3: Movies and Shows open on shelves
`web-app/src/screens/Browse.tsx` (+ components).
- Default view: a search field and one **Filter** button on a single line, then shelves (one horizontally scrolling row per shelf, "See all" tile at the end). Selecting "See all" or applying any filter switches to the existing grid.
- The Filter sheet (modal on phone, side panel on wide screens) holds category, genre, source, sort, language; an active-filter summary chip row appears under the search field with a Clear button.
- Discover (TMDB) moves into Search results ("Not in your library") instead of a toggle on the library screens.

### Task 4: Navigation that explains itself
`web-app/src/components/Rail.tsx`, shell layout.
- Destinations: Home, Search, Live TV, Movies, Shows, My List (favorites + watchlist + history as tabs), and **More** (Sports, Requests, Recordings, Sources, Users, Settings; each shown only when the user may use it).
- Phone: a five-item bottom bar with icon and label (Home, Search, Live TV, Library, More); Library opens a Movies/Shows switch. Wide screens: the rail shows labels when focused or hovered and uses a distinct icon per destination.
- Move "Download Android APK" to Settings → About.

### Task 5: Live TV without the noise
`web-app/src/screens/LiveTV.tsx`, `Guide.tsx`.
- Hide language and region rows when they offer one choice. Channel groups become a horizontally scrolling chip row (All, Favorites, then groups). The preview pane is hidden on phones; selecting a channel plays it.

### Task 6: Title page on a phone
`web-app/src/screens/Title.tsx`.
- Primary actions in one row (Play, Watch together when available, My List), the rest under a "More" menu; audio and version pickers collapse to one "Audio & version" control showing the current choice.

### Verification
- [ ] Playwright screenshots at 390×844, 820×1180 and 1920×1080 for Home, Movies, a title, Live TV; keyboard-only walk on desktop; on the dev instance with real data, the first screenful of Home and Movies shows posters from at least three different shelves.
