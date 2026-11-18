# Title page and player revamp — design

Approved by the owner on 2026-11-18 from mockups `1-episode-page.png`, `2-episode-page-scrolled.png`
and `3-player.png` (same folder). The target is Plex's episode page and player, done better. Everything
here ships on **both** the Android TV app and the web app (browsers and phones).

Two feature branches, one draft PR each:

- `feat/title-page`: the title/episode page, including Activity, Cast & Crew, More like this.
- `feat/player-revamp`: the player look, Skip intro / Skip credits, Up next with autoplay, Still
  watching, the autoplay setting, scrub thumbnails, and the mini player. The TV part is built in
  `feat/player-revamp-tv` and folded into `feat/player-revamp` before the PR is opened.

## 1. Title page (`feat/title-page`)

The TV browses through the web app in a WebView, so this is one page for TV, browser and phone.
It must stay fully usable with arrow keys / remote (the app's spatial focus system) and on a phone.

Layout, top to bottom (mockup 1):

- **Picture**: a landscape 16:9 image on the left: the episode's own still for an episode, the
  backdrop for a film or show (fall back to the poster when nothing landscape exists). A progress bar
  and "N min left" (or "Watched") sit under it when there is progress.
- **Titles**: for an episode, the **show name** is the big title and is a link to the show
  (chevron), the episode name is the subtitle, and a **"Season N" chip** links to that season,
  followed by "Episode E of M". Back from an episode goes to its season, from a season to the show
  (browser history permitting; the links are always there). Films and shows keep their own title.
- **Meta line**: content rating chip, air/release date, runtime, rating.
- **Actions**: the primary orange button with text ("▶ Play", "▶ Resume · 45 min left"), then
  **icon-only glass buttons** (no text; each has an accessible label and a tooltip/focus label):
  Start over (only when there is progress), Mark watched / unwatched, My List, and **⋯ More**.
  The ⋯ menu holds the seldom-used actions: Watch together, Play next episode (episodes),
  Favorite, Audio · <current>, Version · <current source>, Trailer, Request (when relevant). The
  audio chips, "Choose source" and "Available on" blocks go away from the main page; their choices
  live in the menu (opening the existing pickers).
- **Overview**, then a short **spec list** (Video, Audio, Subtitles where known).
- **Season strip** (episodes and seasons): the season's episodes as landscape stills with number,
  name and runtime; the current episode outlined; progress bars; watched ticks. Season switching
  stays (chips or the picker for long series).
- Scrolled further down (mockup 2):
  - **Activity**: other profiles of this household and what they did with this title (watching
    SxEy with progress, finished, added to My List), newest first, at most 6. Only profiles this
    viewer can see on "Who's watching"; adult titles never show activity; kids profiles do not see
    adult profiles' activity. Hidden when empty.
  - **Cast & Crew**: round photos, name, character (TMDB cast already in the detail payload).
  - **More like this**: TMDB recommendations for the title (movie or tv), as posters; ones already
    playable here get the green tick (existing availability badges). Adult rules as everywhere.
    Cached like other TMDB calls.
- No layout collisions at 1920×1080 (TV), 1280×720, and phone widths; nothing overflows
  horizontally except the scroll rows.

Server additions: `parent` links for episodes/seasons (show id + title, season id + number,
episode number, episode count) in the title detail; `activity` and `similar` fields (or separate
endpoints the page loads after the detail, so the page does not wait on TMDB).

## 2. Player (`feat/player-revamp`)

### Shared contract (server → both clients)

`PlaybackDescriptor` gains optional fields (old clients ignore them):

- `markers: list[{type: "intro" | "credits", start_ms: int, end_ms: int}]` — from Plex
  (`/library/metadata/{id}?includeMarkers=1`, `Marker` elements of type intro/credits). Empty for
  other sources and when Plex has none. Never blocks or fails playback (fetch errors → `[]`).
- `show: {id: str, title: str} | null`, `season_number: int | null`, `episode_number: int | null`
  for episodes.
- `next: UpNext | null` and `previous: UpNext | null` for episodes, where
  `UpNext = {id: str, title: str, subtitle: str, image: str | null, duration_ms: int | null}`.
  `id` is a hub id `/playback/resolve` accepts; `subtitle` like "S2 · E2"; next crosses into the
  following season's first episode. Prefer the same library copy as the current episode; use the
  merged season list (see `CatalogService._merged_seasons`) so a season held by another copy still
  counts. Adult and rating rules apply as to any resolve.
- `thumbnails: {url: str, interval_ms: int} | null` — scrub previews. For Plex parts that have
  preview thumbnails (`Part.indexes == "sd"`), the server fetches the part's BIF index
  (`/library/parts/{part_id}/indexes/sd`), caches it, and `GET <url>?ms=<position>` returns the
  nearest frame as `image/jpeg`. `url` is a signed, viewer-independent image grant like other
  artwork (`image_grants`). `null` otherwise (preview thumbnails are currently off on the owner's
  Plex; the feature lights up once they are generated — do not change Plex settings).

Per-profile preference in the existing `/profiles/{id}/prefs` store: `autoplay_next: bool`
(missing = true). Both clients read it when the player opens; the web settings screen and the TV
player's settings get a toggle "Autoplay next episode".

### Behaviour (identical on TV and web)

- **Skip intro**: while position is inside an intro marker, a "Skip intro" button shows bottom-right;
  it seeks to the marker end. On TV, when the controls are hidden, OK activates the visible skip
  button (Netflix-style); it hides itself when the marker ends.
- **Credits / Up next** (episodes with `next`):
  - At the start of the credits marker (the last `credits` marker), show "Skip credits" and the
    **Up next** card (next episode's still, "Up next · S2 · E2", title, 10-second countdown ring).
  - Without a credits marker, the Up next card appears in the **last 30 s** (no Skip credits).
  - At 0, or on Skip credits, the next episode plays in the same player (resolve `next.id`, carry
    the chosen audio language), reporting the finished episode as completed.
  - Back / Escape / "Keep watching" or any seek dismisses the card; the episode then plays to its
    end and the card returns with a fresh 10-s countdown at the very end.
  - With `autoplay_next` off: the card shows a "Play next" button and no countdown.
  - **Still watching?**: after 3 episodes in a row that started by autoplay with no key press, click
    or touch in between, the 4th does not start; the player pauses on a "Still watching?" prompt
    (Continue watching / Back to the show).
- **Films** with a credits marker: "Skip credits" ends playback (completed) and returns.
- Live channels, recordings, catch-up and watch rooms: no skip/up-next behaviour.

### Look (mockup 3)

Top: show · season · episode line and the episode title (film: title), a close button top-right.
Bottom: a wide timeline (orange watched, lighter buffered) with intro/credits ranges marked, a
scrub thumbnail with the time above the knob while seeking (when `thumbnails` exists, else just the
time), elapsed left / remaining right. Control row: left = audio and subtitles (glass icon + current
value, opening the existing pickers); centre = previous episode, back 10 s, play/pause (large, white),
forward 30 s, next episode (episode buttons only for episodes; live keeps its own channel controls);
right = Watch together (where the current player already offers it), settings. Glass circular
buttons, Inter-like type, the app's accent orange. Controls auto-hide; existing keyboard/remote
mappings and the gamepad mapping keep working.

### Mini player

- **Web**: a "minimise" control (and Back from the player while VOD is playing) shrinks playback into
  a bar along the bottom of the app (mockup 1): picture, title + S·E + time, previous/−10/play-pause/
  +30/next, and on the right Watch together, audio, expand (back to full screen) and close. Playback
  continues while the user browses (the player lives above the router). One mini player at a time;
  starting another title replaces it. Live channels may use it too. Phone: a compact one-row bar.
- **TV**: Android picture-in-picture (`enterPictureInPictureMode`, supported on the target device,
  API 34). Minimise (a button in the controls, and Back while VOD plays) puts `PlayerActivity` into
  PiP; the WebView app shows underneath with the same bottom bar rendered by the web app, driven over
  the existing shell bridge (play/pause, ±seek, next/previous, expand = leave PiP / bring the player
  back full screen, close = stop). The bar shows only while the native player is in PiP. Use the
  existing Media3 session where it helps.

### Implementation notes (`feat/player-revamp`)

- Server: the extras are looked up beside the resolve and given 4 s; anything late or failing is left
  out (`markers: []`, nulls). They are empty/null for live channels, recordings, trailers and **watch
  rooms** (a room plays on together, so its descriptors carry none). `season_number`/`episode_number`
  are set for every episode even when the rest is missing.
- `thumbnails.interval_ms` is the index's real frame spacing once the server has read the BIF (it
  prefetches it when it hands out the URL), else 2000 (Plex's default). The server always answers the
  frame at or nearest before `ms`, so the interval only serves rounding for cache sharing.
- Frames are served from `GET /api/v1/thumbnails/{token}?ms=<position>` (`image/jpeg`, cacheable until
  the grant's week ends, 404 when the index cannot be fetched or parsed).

### TV mini-player bridge (web half in this branch, native half in `feat/player-revamp-tv`)

Same envelope conventions as `play()`: version 1, a `kind`, JSON text through the existing objects.
Unknown kinds and malformed messages are ignored by the receiver.

- TV → page: `window.streamhubShell.message(json)` (returns `true` when understood) with
  `{"v":1,"kind":"mini_player","state": null | {"id":str,"title":str,"subtitle":str|null,
  "image":str|null,"position_ms":int,"duration_ms":int|null,"playing":bool,"has_next":bool,
  "has_previous":bool,"live":bool}}`. `null` hides the bar. The TV sends it on entering PiP, on every
  change, at least every ~5 s while playing, and `null` on leaving PiP or stopping. The page also hides
  the bar when `playbackEnded()` is called. Between reports the page runs the clock on by itself.
  `image` must be an absolute http(s) URL or a same-origin path; anything else is not shown.
- Page → TV: `window.StreamHubTV.message(json): boolean` with
  `{"v":1,"kind":"mini_player_command","command":"play_pause"|"seek_by"|"next"|"previous"|"expand"|"close",
  "delta_ms":int|null}` (`delta_ms` only for `seek_by`, negative is back: the bar sends −10000 and
  +30000). `expand` leaves PiP / brings the player back full screen; `close` stops playback. `message`
  is optional on the bridge: without it the page never shows the bar.
