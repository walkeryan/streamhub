# StreamHub TV design brief + UAT acceptance criteria

Basis: Plex's visual language (dark, artwork-first, hero + rows) with the classic **left rail** Plex reinstated in Aug 2026 after the top-bar redesign was rejected. Reference products: Google TV "For You" (service badges on tiles), Apple TV app (hero + Up Next), Netflix rows, TiviMate/OTT Navigator (live TV ergonomics).

## Navigation
- Collapsible left rail: Home, Search, Live TV, Guide, Movies, Shows, Favorites, Sources, Settings. Profile avatar at bottom.
- Left from the first card in any row opens the rail; Back collapses it. Rail shows icons only when collapsed, icon+label expanded.
- Back always returns focus to the element that opened the current screen (focusRestorer on every row; remembered row index per screen).
- Any title reachable in ≤5 presses from Home.

## Home
- Hero billboard (full-bleed backdrop, gradient scrim bottom+left, logo or display title, 1-line meta, source badges, Play/Details buttons). Auto-advance every 8 s, paused while a hero button is focused.
- Rows in order: Continue Watching · On Now (favorite channels, EPG progress bar) · Recently Added · Watchlist · Favorites · then per-source/genre rows. User can reorder/hide rows in Settings. No promotions, ever.
- Focusing a poster updates the hero background (crossfade 420 ms) and meta text.

## Tiles
- VOD: 2:3 poster, title below only when focused or when no artwork. Live: 16:9 with logo on blurred colour block, channel number, now-playing progress bar.
- Source badges: small chips bottom-right (Plex / Jellyfin / IPTV / Netflix…). "Play" chip for in-app sources, "Open" chip for hand-off.
- Focus: scale 1.08 + 3 dp ring in `focus_ring` + glow in `focus_glow`, 180 ms ease-out. Never colour-only.

## Title detail
- Backdrop with scrim, poster left, title/meta/overview right, action row: **Play** (resumes if progress>0; label shows "Resume · 42 min left"), source picker if >1 playable source, Watchlist, Favorite, Trailer (YouTube hand-off), Mark watched. Seasons/episodes rows below for shows. "Available on" row listing in-app sources first, then hand-off services, with JustWatch attribution.
- Pressing Play on an in-app source starts playback with no intermediate screen.

## Live TV & Guide
- Live TV screen: channel list left (number, logo, name, now/next), preview player right that plays the focused channel after 600 ms dwell; language chips and group list across the top (chips = profile languages; "All" available).
- Guide: classic horizontal EPG grid, 30-min columns, now-line, 2 h visible, long-press/OK on past programme offers Catch-up when available.
- Player overlays: channel OSD on zap (number, name, now/next, progress, auto-hides 4 s), number keys with 2 s commit, Up/Down zap, Back-twice = last channel, OK = mini list overlay, Menu = audio/subtitle/aspect/sleep timer.

## Search
- Left: text field (voice via system keyboard), recent searches; right: results in three groups — Play here, Open in app, Channels. Debounce 400 ms. Keyboard never covers results.

## Sources / onboarding
- First run: disclaimer gate (docs/LEGAL.md) with explicit Accept; Add Source cards for Plex (PIN link + QR), Jellyfin, Xtream, M3U, Streaming services (toggle grid of Netflix, Hulu, Disney+, Prime, Max, Apple TV+, Peacock, Paramount+, YouTube, Tubi, Pluto).
- TV pairing: TV shows 6-char code + QR; user approves on web.

## Themes
Midnight (default), OLED Black, Daylight, High Contrast, Poster Adaptive — per profile, live-switchable in Settings with preview. All built from design/tokens.json.

## Performance budget
Focus move ≤100 ms · Home first frame ≤1.5 s on a 2 GB stick · images cached (Coil) and sized to tile · no blur on scrolling content (blur only on static overlays).

## UAT checklist (design-review agent scores each)
1. Focus visible from 3 m; ring contrast ≥3:1 against neighbours; never colour-only.
2. Text contrast ≥4.5:1 body, ≥3:1 large; body ≥16 dp (≈22 px) on TV.
3. Nothing interactive outside the 48/27 dp safe area; backgrounds bleed to edges.
4. Every screen fully operable with D-pad + Back + OK only; no dead ends; Back restores focus.
5. Hero/rows match this brief; no promotions; user's sources first.
6. Play on an in-app title starts the stream directly; hand-off titles are labelled "Open in app".
7. Themes switch without restart; High Contrast passes WCAG AA; reduced motion honoured.
8. Live TV: zap OSD, number entry, favorites filter, language filter all work.
9. Disclaimer gate blocks adding IPTV/M3U until accepted; TMDB + JustWatch attribution visible.
10. Motion 120–260 ms ease-out; no layout shift on focus.
