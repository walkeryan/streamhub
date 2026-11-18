# App-wide restyle — design system and phase 2 checklist

The owner approved the new title page and player (`2026-11-18-title-and-player-design.md`) and asked
for "the same for the rest of the app, with the glass icons and everything else". The web app is also
what the Android TV app shows in its WebView, so this restyles TV, desktop browsers and phones at once.

- **Phase 1** (`feat/app-restyle`, this document): the design system, extracted from the title page
  and player (which now use it), the app shell and Home.
- **Phase 2**: three agents restyle the remaining screens in parallel with these building blocks, each
  on its own branch off `feat/app-restyle`, merged back into it before one PR is opened.

Owner decisions that still hold: wide screens use a **top bar**, phones keep the **bottom bar**, and
there is **no left side rail**.

## 1. Files and who owns them

| File | Holds | Owner |
|---|---|---|
| `src/ds/` (`index.ts`, `icons.tsx`, `GlassIconButton.tsx`, `OverflowMenu.tsx`, `Section.tsx`, `Tiles.tsx`, `States.tsx`) | design-system components | phase 1 — **do not edit** |
| `src/styles/ds.css` | tokens and every shared component's styles | phase 1 — **do not edit** |
| `src/styles/core.css` | reset, the `.fx` focus look, utilities, pad glyphs | phase 1 — **do not edit** |
| `src/styles/shell.css`, `src/components/Rail.tsx`, `src/components/SearchBox.tsx`, `src/App.tsx` | top bar, bottom bar, page background, toasts | phase 1 — **do not edit** |
| `src/styles/home.css`, `src/screens/Home.tsx`, `src/components/Notices.tsx`, `src/components/WatchingNow.tsx` | Home | phase 1 — **do not edit** |
| `src/styles/title.css`, `src/screens/Title.tsx`, `src/screens/title/*` | title page | phase 1 — **do not edit** |
| `src/styles/player.css`, `src/styles/mini.css`, `src/screens/Player.tsx`, `src/player/*` | player, mini player | phase 1 — **do not edit** |
| `src/components/ui.tsx`, `Tile.tsx`, `Icon.tsx`, `ChipMenu.tsx`, `FeatureCard.tsx`, `SafeImg.tsx`, `RequestStatus.tsx`, `PadHints.tsx`, `playAction.ts` | shared primitives the design system re-exports | phase 1 — **do not edit** |
| `src/components/LanguageFilter.tsx`, `CatalogPicker.tsx`, `Avatar.tsx`, `src/recordings/RecordDialog.tsx`, `src/recordings/RecordingBadge.tsx`, `src/room/ShareSheet.tsx`, `src/room/JoinRoomDialog.tsx` | shared by more than one group | **do not edit** (ask) |
| `src/styles/base.css` | the import list only | **do not edit** |
| `src/styles/live.css` | Live TV, guide, channel list, Sports | phase 2 **group A** |
| `src/styles/library.css` | Movies & Shows, Discover, Search, My List, Requests, Recordings | phase 2 **group B** |
| `src/styles/account.css` | Settings, people, sign-in, pairing, Who's watching, rooms | phase 2 **group C** |
| `design/tokens.json`, `src/styles/tokens.css`, `src/tokens.ts` | theme colours shared with the native TV app (generated) | nobody |

The rules that used to sit in `base.css` for each area were moved, unchanged, into that area's file.
A phase 2 agent edits only its screens, its components (section 6) and its area stylesheet. When it
needs something from a do-not-edit file (a new icon, a variant, a token, a fix), it **does not edit the
file**: it lists the request in its report (what, why, which screen) and works around it locally in
its own area file if it must, with a comment `/* ds-request: … */` so it can be folded in later.
Never redefine a `ds-*` class, `.btn`, `.chip`, `.panel`, `.badge`, `.tile*`, `.dialog` or
`.chip-menu*` in an area file; add a screen class beside it instead (`className="btn live-watch"`).

Import building blocks from one place:

```ts
import { Button, Chip, GlassIconButton, OverflowMenu, Section, SectionHeader, PageHeader, HRow,
  StillCard, Captioned, GlassPanel, Empty, ErrorState, SkeletonRow, Modal, Icon } from '../ds'
```

## 2. Tokens (`src/styles/ds.css`, CSS variables on `:root`)

Sizes are rem; the root font scales with the window (16 px at 1280, 20 px at the mockups' 1600, 24 px
on a 1920 TV, 15 px on a phone), so one layout serves every screen. Colours come from the theme
(`--t-bg`, `--t-surface`, `--t-surface-alt`, `--t-text`, `--t-text-muted`, `--t-accent`,
`--t-accent-text`, `--t-focus-ring`, `--t-danger`, `--t-success`; five themes, generated). Never hard-code
a theme colour; white is only for things over a picture.

| Group | Tokens |
|---|---|
| Glass | `--glass-ink` (what glass is tinted from: the theme's text colour, white inside `.ds-on-media`, `.player`, `.mini-bar`), `--glass-tint` (flat glass: chips, panels), `--glass-tint-hi`, `--glass-fill` / `--glass-fill-hi` (the gradient of buttons), `--glass-line` (1 px edge), `--glass-sheen` (inset top highlight), `--glass-blur` |
| Popovers | `--pop-bg`, `--pop-line` (menus, dialogs, toasts) |
| Surfaces | `--surface-edge`, `--surface-sheen`, `--surface-shadow` |
| Radii | `--r-xs` 0.25 (rating box) · `--r-sm` 0.45 (menu items, badges) · `--r-md` 0.6 (buttons, stills) · `--r-lg` 0.7 (tiles, cards) · `--r-xl` 0.9 (panels, dialogs) · `--r-pill` |
| Shadows | `--shadow-raise` (glass buttons), `--shadow-card` (tiles), `--shadow-hero` (the title page's picture), `--shadow-pop` (menus, dialogs), `--shadow-accent` (the primary button) |
| Type | `--fs-hero` 2.2 · `--fs-page` 1.6 · `--fs-sub` 1.3 · `--fs-section` 1.2 · `--fs-body` 0.9 · `--fs-meta` 0.8 · `--fs-small` 0.75 · `--fs-micro` 0.7 (phones raise body/meta/small to 1 / 0.875 / 0.8125). Classes: `.ds-page-title`, `.ds-section-title`, `.display`, `.title-xl`, `.heading`, `.label`, `.muted` |
| Spacing | `--page-x` / `--page-y` (the page's side and top padding: the TV safe area, 1rem on a phone), `--gap-xs` 0.3 · `--gap-sm` 0.5 · `--gap-md` 0.7 (between action buttons) · `--gap-row` 0.9 (between tiles) · `--gap-lg` 1.4 · `--gap-section` 2.2 (between sections) |
| Controls | `--ctl-sm` 2.3 · `--ctl-md` 2.9 · `--ctl-lg` 3.6 (glass button sizes; never under 44 px), `--chip-h` 2.125 |
| Focus | `--ring-w`, `--ring-color`, `--glow-accent`; `--focus-scale`, `--focus-border` (theme) |
| Tiles | `--still-w` (landscape still width; 11.8 rem, 14 in Home's rows, 13 / 12 on a phone), `--tile-poster-w/h`, `--tile-channel-w/h` |
| Shell | `--bar-h` (what the top bar takes; 0 on a phone), `--bottom-bar-h` |

## 3. Components

| Component | Where | Use |
|---|---|---|
| `Icon` | `ds/icons.tsx` | The one icon set: 24-unit grid, round 1.9 strokes. `ICON_NAMES` lists them. Add new glyphs there (ask). No emoji as icons. |
| `GlassIconButton` | `ds/GlassIconButton.tsx` | Round glass, icon only, for **secondary** actions. `label` (required) is read by assistive tech and shown as a tooltip under it on focus/hover (`tip` overrides the words, `tipAt="above"` near the screen's foot, `tip={false}` for none). `size` sm / md / lg, `pressed` (accent), `fillWhenPressed`, `disabled`, `expanded` + `popup` for a menu button. Phones hide tooltips. |
| `Button` | `components/ui.tsx` | Text button. `primary` = the orange button, **one per screen or dialog**, the main action, with words (and an icon first when it plays: `<Icon name="play" />`). Plain = secondary glass text button where words are needed (forms, dialogs). `ghost` = Cancel / Close. `lg` = hero and title size. `danger` for destructive. |
| `Chip`, `ChipMenu` | `components/ui.tsx`, `ChipMenu.tsx` | Choices and filters: glass until pressed, then accent. `ChipMenu` drops its options down (sheet on a phone). `.btn.btn-chip` makes a button match chips. |
| `.ds-pill`, `.ds-pill-accent`, `.ds-pill-live`, `.ds-rating`, `.ds-meta`, `.ds-star` | `ds.css` | A small glass label or link in a line ("Season 2", "On now", "LIVE"); the content-rating box; a muted meta line (date · runtime · ★ rating). |
| `OverflowMenu` | `ds/OverflowMenu.tsx` | The ⋯ glass button and the seldom-used actions behind it: a popover under the button on TV/desktop, a sheet with Close on a phone. Items: `{ key, icon, label, onEnter, testId?, pressed?, disabled?, group?, keepOpen?, fillWhenPressed? }`; a rule separates groups. Focus keys: button `focusKey`, list `menuKey`, items `${menuKey}:${key}`. |
| `Popover` | `ChipMenu.tsx` | Any other list drawn over the page from a button (traps Back and arrows, restores focus). |
| `Section`, `SectionHeader`, `PageHeader` | `ds/Section.tsx` | Every heading: name, then a quiet count/note (`meta`), then an `action` at the far end. `PageHeader` is a screen's own title line (`h1`, page size) with its tools as children. One heading size per level across the app. |
| `HRow` | `ds/Section.tsx` | A sideways-scrolling row bleeding to the window edges inside the page padding; remembers the last focused tile. |
| `TileRow` | `components/Tile.tsx` | A full-width row of tiles for titles (Home, Movies & Shows shelves): `SectionHeader` + `HRow` + See all. Landscape rows draw `StillCard`s, poster rows `MediaTile`s, channel rows `ChannelTile`s. |
| `StillCard` | `ds/Tiles.tsx` | A landscape still with its words under it: progress along its foot, watched tick, `current` outline, `badge` top-left, `corner` top-right. Episodes, Continue Watching, recordings, catch-up. |
| `MediaTile` | `components/Tile.tsx` | A poster tile: name over its foot on focus, where-it-plays badge, favourite, progress; `mark="availability"` gives the green tick only (search, More like this). |
| `Captioned` | `ds/Tiles.tsx` | Puts a name under any tile instead of over it (More like this, result grids). |
| `ChannelTile` | `components/Tile.tsx` | A channel: logo box, name and what is on, now-progress. |
| `WatchedTick`, `AvailableTick`, `.badge`, `.badge-play`, `.badge-ok`, `.badge-warn`, `.rec-badge` | `ds/Tiles.tsx`, `ds.css` | Tile and row status marks. `.badge-play` (plays here) is a dark pill with a green dot. |
| `GlassPanel` / `.panel` | `ds/States.tsx`, `ds.css` | A frosted card (settings groups, notices, activity). `tone="accent" | "danger"`. `.panel` is the slightly more opaque card older screens use; both are fine. |
| `Modal` | `components/ui.tsx` | Dialogs. `variant="sheet"` rises from the bottom on a phone (with a grab handle); `variant="panel"` also docks right on wide screens. Title in the section size. Traps Back, restores focus. |
| `Empty`, `ErrorState` | `components/ui.tsx` | Empty and error states: a glass disc with an icon (`icon`), the title, a hint, then the way forward (buttons as children). |
| `Skeleton`, `SkeletonRow` | `components/ui.tsx`, `ds/States.tsx` | Loading: shapes where the content will be (`SkeletonRow layout="poster" | "landscape" | "channel"`). |
| `Toasts` | `components/ui.tsx` | Frosted card with the tone's icon (info / success / error), bottom right (above the bar on a phone). Call `useUi().toast(text, tone)`. |
| `.ds-on-media` | `ds.css` | Wrap controls that sit over a picture or video: glass turns white. |
| `.under-bar` | `shell.css` | A backdrop that runs up under the clear top bar (Home's billboard and the title page do; most screens should not). |

## 4. Rules

1. **Text only on the primary action.** One orange `Button primary` per screen (or dialog): Play,
   Resume, Save, Record, Join. Everything else that is an action is a `GlassIconButton` with a label
   (tooltip on focus), unless it truly needs words (a form's secondary choice, Cancel: plain/ghost
   `Button`).
2. **Seldom-used actions go into ⋯.** More than about four secondary actions, or actions most viewers
   never use (Version, Trailer, Request, Remove, Rename, Reset), go behind `OverflowMenu`.
3. **Headings** are `PageHeader` (screen title, tools at its end) and `SectionHeader` (blocks). No other
   heading sizes; counts and notes go in `meta`, not in the title text.
4. **Spacing**: pages pad by `--page-x` / `--page-y` (`.page-pad`); sections are `--gap-section` apart;
   action buttons `--gap-md`; tiles `--gap-row`. Nothing scrolls sideways except rows (`HRow`,
   `.chip-scroll`); no horizontal page overflow at any width.
5. **Surfaces**: glass (`--glass-*`) for controls and light cards; `--pop-bg` for anything over the page
   (menus, dialogs, toasts). Colours from the theme only; test with a light theme too.
6. **Tiles**: landscape stills for things in progress and episodes (`StillCard`), posters for titles
   (`MediaTile`), channel tiles for channels. Progress is the accent bar along the picture's foot;
   watched is the green tick in a dark disc; plays-here is the green dot / solid green tick.
7. **Icons** come from `Icon` only; no emoji, no inline SVG in screens.
8. **Focus and the remote**: everything actionable is a `Focusable` (or a component built on it) inside
   a `FocusGroup`; keep existing `focusKey`s and `data-testid`s (tests and Back/restore logic use them).
   Rows are `HRow`/`FocusGroup`s so Up/Down moves between rows and Left/Right along one. Back closes
   menus/sheets first (`Modal`, `Popover`, `OverflowMenu` do it). Focus must always be visible: the
   `.fx` ring, or the component's own focus style; never remove it without a replacement.
9. **Phones**: 44 px minimum touch targets (the glass sizes enforce it), sheets instead of popovers
   (`OverflowMenu`, `ChipMenu`, `Modal variant="sheet"` do it), no tooltips, the bottom bar stays clear
   (`.main` already pads for it).
10. **Behaviour stays**: restyle presentation only; keep routes, data, focus keys, test ids, and
    accessible names (a button that loses its words keeps them as `label`). Update a spec only when it
    asserted old markup, never to hide a behaviour change.

## 5. The shell and Home (done in phase 1)

- **Top bar** (wide screens): "Stream**Hub**" wordmark, the six places by name (current one with an
  accent underline), the glass search pill, the ⋯-style glass More button, the avatar. Clear over the
  top of a page; frosted with a hairline once the page scrolls (`html[data-scrolled]`); solid on a TV
  (≥1800 px) and where transparency is reduced. Remote focus on an entry: accent ring and tint.
- **Bottom bar** (phones): frosted, five entries (icon over name), the current icon in an accent pill.
- **Page background**: the theme's background with a faint fixed accent glow (`.shell::before`).
- **Toasts**: frosted cards with an icon per tone.
- **Home**: the billboard runs under the clear bar (art fades into the page), title or logo, one meta
  line, where-it-plays badge, orange Play and a glass Details button, carousel dots; Continue Watching
  as landscape stills with name, episode/year and progress; On Now channel tiles; poster shelves with a
  glass See all card; notices and public rooms as glass banners (dismiss is a glass ✕).

## 6. Phase 2 checklist

Each group: branch `feat/app-restyle-<group>` off `feat/app-restyle`; edit only its screens, its
components below and its area stylesheet; small commits; run the checks in section 7; screenshots at
1920×1080, 1280×720 and 390×844 of every screen it touched; report requests for do-not-edit files.

For **every** screen: `PageHeader` for the screen title (tools at its end); `SectionHeader` for blocks;
one primary button at most; other actions glass icon buttons (labels kept) or ⋯; glass chips for
filters; `GlassPanel`/`.panel` cards; `Empty`/`ErrorState` with an icon; `SkeletonRow`/`Skeleton` while
loading; phone layout checked; remote walk checked (arrows, OK, Back) with focus always visible; no
horizontal overflow.

### Group A — Live & Sports (owns `styles/live.css`, `components/ChannelRow.tsx`, `screens/useChannelFeed.ts`, `useGuideView.ts`, `useRowView.ts`)

- [ ] **LiveTV**: header (title, view switch as chips, search and Options as glass icon buttons — they
  are `btn-chip btn-icon` today), group chips in `.chip-scroll`; Options sheet as `Modal variant="sheet"`.
  Keep the fixed-height layout (`.live-screen[data-view]`) and the guide/channel virtualisation.
- [ ] **Guide** / **GuideRow**: grid cells on glass (`--glass-tint`), now cell accent-tinted, now-line
  stays; channel column with `.logo-block`; keep `ROW_REM` = `--epg-row` and the sticky header/column.
- [ ] **GuideDetails**: the focused programme beside the header: title, `.ds-meta`, Watch (primary) and
  Record (glass icon, label "Record"), the sheet on narrow screens.
- [ ] **Channels**: list rows (`ChannelRow`: also used by the player's channel list — keep its props and
  `data-testid="channel-row"`), preview pane as a glass panel with the primary Watch.
- [ ] **Sports**: live games as cards, timetable rows, networks; day/sport chips; glass icon buttons
  for previous/next day; sheets for "which channel".
- [ ] **SportsMatchup**: the matchup card art stays (it is generated art); badges as `.ds-pill-live` /
  `.badge`; keep the cqi sizing.

### Group B — Library (owns `styles/library.css`, `components/BrowseGrid.tsx`, `CatalogMode.tsx`, `AdultUnlock.tsx`, `src/recordings/*` except `RecordDialog.tsx` and `RecordingBadge.tsx`)

- [ ] **Browse** (Movies & Shows): `PageHeader` with Filter (glass text button or glass icon with
  label "Filter": it has a test id `browse-filter`), active filters as chips, shelves are `TileRow`
  (done), the grid (`.grid-tiles`) with `MediaTile`; the Filter panel `Modal variant="panel"`.
- [ ] **Discover**: same grid; paging buttons as glass icon buttons (chevL / chevR) with labels.
- [ ] **Search**: the phone's box as a glass field; result sections with `SectionHeader` and counts in
  `meta`; broadcast/channel rows (`.search-row`) as glass rows; poster grid with `mark="availability"`.
- [ ] **Favorites** (My List): `PageHeader` with the tabs as chips; grid; `Empty` with icon `bookmark`.
- [ ] **Requests**: tabs as chips; each request a glass panel row with `RequestStatus`, Details as the
  row's action; approve/decline (admins) — approve primary, decline into ⋯ or ghost.
- [ ] **Recordings**: tabs as chips; recordings as `StillCard`s or glass rows; Delete and rule edits into
  ⋯; `Empty` states with icon `record`.

### Group C — Account & people (owns `styles/account.css`, `components/` AndroidDownload, ArrowPin, ContentPolicySettings, DeviceRow, HomeNetworkSettings, HomeShelvesSettings, LinkToCopy, OwnerPin*, PeopleControls, PinPad, ProfileSwitch, RequestManagerSettings, SignInMethods, SignInSecret, WhoIsWatching; `src/room/*` except `ShareSheet.tsx` and `JoinRoomDialog.tsx`)

- [ ] **Settings**: `PageHeader`; tabs as chips; groups as `GlassPanel`s with `SectionHeader`s; toggles
  (`.toggle`) restyled in `account.css`; theme cards; Save as the primary where there is one.
- [ ] **Users** / **UserDetail**: people as glass rows (`Avatar`, name, role pill), row actions into ⋯,
  Invite as the primary; dialogs as sheets on a phone.
- [ ] **Sources**: sources as glass panels with status pills; Add as the primary; Test/Remove as glass
  icon buttons or ⋯; `Empty` with icon `source`.
- [ ] **Gate** (Who's watching, disclaimer, away): the profile grid (`WhoIsWatching`) on the page
  background with large avatars and a focus ring on the face; the disclaimer as a glass panel with one
  primary Accept.
- [ ] **Pair**, **Invite**, **Waiting**, **GoogleLogin**: centred glass panel, the wordmark, one primary
  action; codes in large tabular type; steps as a numbered list.
- [ ] **Room** (join screen): the old title layout (`.title-page`) moves to the title page's look:
  backdrop with veil, picture, title, Join as the primary, members as pills. `ShareSheet` and
  `JoinRoomDialog` are shared (ask).

## 7. Checks (all groups)

From `web-app/`, in a node 22 container: `npm run lint`, `npm run typecheck`, the unit tests
(`node --test tests/*.test.mjs`), `npm run build`, `npm run build:mock`. Playwright 1.58 offline
(`mcr.microsoft.com/playwright:v1.58.2-noble`, `--network none`, `PLAYWRIGHT_BUNDLED=1`,
`-c playwright.simple.config.ts`; set `CHOKIDAR_USEPOLLING=1` if the dev server runs out of file
watchers). Compare failures with `feat/app-restyle` in the same setup: no new failures. Known
stale/flaky on main: `rooms.spec.ts:378`, seek-hint timing in `gamepad-player.spec.ts:78` /
`player-controls.spec.ts:533` under parallel load, `people.spec.ts:504`, and the Sports specs that
depend on the wall clock.
