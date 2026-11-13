# StreamHub for Android TV

Native Android TV client for the StreamHub server (`hub-server/`). Kotlin 2.3 · Compose for TV
(`tv-material 1.1.0`, `tv-foundation 1.0.0`) · Media3 1.11 · Hilt · Ktor (CIO) · Coil 3 · DataStore.
minSdk 24, target/compileSdk 36, leanback-only (no touchscreen required).

## Build

Requirements: JDK 17+ (21 tested) and an Android SDK with `platforms;android-36` + `build-tools;36.x`.

```bash
cd tv-app
cp local.properties.example local.properties   # set sdk.dir to your SDK path
./gradlew :app:assembleDebug                    # → app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:assembleRelease                  # minified; signed with the debug key for sideloading
./gradlew testDebugUnitTest                     # JVM unit tests of every module (what CI runs)
```

Headless SDK install (Linux):

```bash
mkdir -p ~/android-sdk/cmdline-tools && cd ~/android-sdk/cmdline-tools
curl -O https://dl.google.com/android/repository/commandlinetools-linux-<rev>_latest.zip
unzip commandlinetools-linux-*_latest.zip && mv cmdline-tools latest
yes | latest/bin/sdkmanager --licenses
latest/bin/sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.1.0"
```

> If `repo.maven.apache.org` rate-limits you (HTTP 429), run Gradle with an init script that
> rewrites Maven Central to `https://maven-central.storage-download.googleapis.com/maven2/`.

### Why these versions (AGP 8.x ceiling)

The project is pinned to **AGP 8.13.2**. As of Oct 2026 several latest AndroidX/Coil/Hilt releases
require AGP 9.1 + compileSdk 37, so the catalog uses the newest versions that still work with AGP 8:

| Library | Pinned | Newest | Reason |
|---|---|---|---|
| Compose BOM | 2026.06.01 | 2026.09.00 | 2026.08+ → Compose 1.12 requires AGP 9.1 |
| Navigation | 2.9.8 | 2.10.x | 2.10 requires AGP 9.1 |
| Lifecycle | 2.10.0 | 2.11.0 | 2.11 requires AGP 9.1 |
| Hilt | 2.58 | 2.60.1 | 2.59+ plugin refuses AGP < 9 |
| Coil | 3.5.0 | 3.6.3 | 3.6 pulls Compose 1.12 |
| Media3 | 1.11.1 | — | as requested |

`kotlin-metadata-jvm 2.4.21` is added to the KSP classpath because Dagger 2.58's bundled copy
can't read the Kotlin 2.4 metadata in kotlinx-coroutines/serialization 1.11. Moving to AGP 9
means bumping these together.

## Sideload onto a TV

1. On the TV: **Settings → Device Preferences → About → Build** (press OK 7×) to enable developer
   options, then **Developer options → USB/Network debugging → On**.
2. Find the TV's IP (Settings → Network) and connect:

   ```bash
   adb connect 192.168.1.50:5555          # accept the prompt on the TV
   adb install -r app/build/outputs/apk/debug/app-debug.apk
   adb shell am start -n com.streamhub.tv.debug/com.streamhub.tv.MainActivity
   ```

   Debug builds install as `com.streamhub.tv.debug` alongside a release build (`com.streamhub.tv`).
3. The app appears in the TV launcher's Apps row with the StreamHub banner.

## Point the app at your server

On first launch:

1. **Disclaimer** (docs/LEGAL.md v1.0), shown before anything else: *I accept* is focused by default
   but stays disabled for 3 s. *Exit* closes the app. The built-in copy is used until a server is known;
   if the server later serves a newer version, the gate is shown again before pairing. A TV that was
   paired without accepting (older build) gets the gate on next launch and then goes straight to Home.
2. **Server address**: enter the StreamHub host, e.g. `192.168.1.10:8787` (the Unraid IP and port).
   `http://` and `:8787` are added if you leave them out, and `/api/v1` is appended automatically. The
   app calls `GET /api/v1/health` before it continues.
3. **Pair**: the TV shows a 6-character code and a QR code (`<server>/pair?code=XXXXXX`). Approve it
   from the web app while signed in. The TV polls `POST /auth/device/poll` and continues automatically.

To change server later: Settings → Playback & server → *Sign out of this TV*, then enter the new address.
Cleartext HTTP is allowed (`network_security_config.xml`) because the server and most IPTV upstreams are on the LAN over plain HTTP.

## What the app does

Everything is driven with the D-pad, Select and Back.

- **Home, Movies, Shows, Discover, Sports, My List, Search**: browse and play what the server's
  sources have; titles that live in another app are handed off to it.
- **Live TV and Guide**: channel list with number keys, groups and languages; EPG grid with catch-up.
- **Who's watching?** on shared TVs: pick a person, with their PIN (below).
- **Watch together**: start a room on a film, a series, an episode or a live channel, or join one
  with its six-character code (below).
- **Recordings**: record one programme, every new episode, or every game for a team; manage and play
  them (below).
- **Requests**: ask for a title no source has; see the answer on Home (below).
- **Updates**: a newer build offered by the server is downloaded, verified and handed to the installer.

### The rail

Top to bottom, which is also the D-pad order:

1. **Home · Search · Live TV · Movies · Shows · My List**
2. the secondary destinations: **Sports · Discover · Join a room · Recordings · Requests · Sources**
3. **Settings**, then the avatar (the picker on a shared TV, Settings → Profiles otherwise)

A thin line separates the three. *Join a room*, *Recordings* and *Requests* are there only for
someone who may use them (table below); a destination that is not offered is absent, not greyed out.
The order and the gating are `RailOrder` in `core/ui/.../LeftRail.kt`, a pure function with tests.
The rail's items sit inside the 48 / 27 dp safe area and the list scrolls with focus when it is
taller than the screen. *My List* is the Favorites screen under the name the web app uses.

### Permissions

On start, after pairing and after a switch of person the app reads `GET /auth/me` and keeps the
answer (`MeRepository`). It is read again whenever the token pair had to be exchanged, which is what
a `401 session_stale` (a role or permission was changed on the server) leads to: the tokens are
refreshed silently and the call is repeated once. Actions the signed-in person may not use are not
shown:

| Needs | Shows |
|---|---|
| `rooms.create` | *Watch together* on a title's page, on an episode and on a live channel |
| `rooms.join` | *Join a room* in the rail and in Search |
| `recordings.schedule` | the Record actions in the Guide and on Live TV |
| `recordings.view` | *Recordings* in the rail (and only when the server records) |
| `requests.create` | *Request* on a title's page; *Requests* in the rail |
| `requests.approve` | *All requests* with Approve / Decline (Settings → Requests for someone who may only approve) |

A server without `/auth/me` (404) is treated as before permissions existed: everything is offered
except what only an administrator could do. Refusals are worded for the screen (`ApiErrors`): no
permission (with what is missing), a suspended account, an account waiting for approval, "this
account can only be used on the home network" (`remote_access_denied`), and "you're already
watching … on another device" for a per-person stream limit.

### When the session ends

| The server says | What the TV does |
|---|---|
| 401 `token_expired`, `session_stale` | exchanges the token pair and repeats the call once, silently |
| 401 `session_revoked` (signed out elsewhere, or the session changed hands) | drops the tokens, keeps the server address, explains in a dialog and goes back to pairing |
| 401 `user_suspended` | keeps the pairing and explains once. A shared TV then returns to the picker, which offers *Pair this TV again* while nobody can be picked; a personal TV stays where it is |

The same holds when the refusal comes from `POST /auth/switch` or from a media URL (see "When the
server refuses a stream").

### Who's watching? (shared TVs)

When `/auth/me` says `shared_device`, the TV belongs to the household rather than to one person:

- **After pairing and on every cold start** the first screen is a grid of the people who can be
  picked here (`GET /auth/switchable`): picture or initial, name, role, and a lock with "PIN" for
  people who have one. This picker cannot be dismissed: Back offers *Exit app*. Picking the person
  who was already signed in still asks for their PIN, if they have one.
- **From the rail's avatar** (and Settings → Profiles) the same picker is a visit: Back returns, and
  picking the current person just closes it.
- **PINs** are typed on an on-screen number pad. The D-pad walks it and the remote's own digit keys
  type into it from anywhere; digits show as dots; 4 to 8 digits. Each failure has its own text: a
  wrong PIN, a person who has to set a PIN first (done in the web app), too many attempts (with a
  countdown from the server's `Retry-After`), or the reason the server could not be reached.
- A switch (`POST /auth/switch`) saves the new tokens, bumps the session generation and clears what
  was cached for the previous person (stream leases, channel lists, permissions).

How each answer of `POST /auth/switch` is handled (`UserSwitchRepository`, tested against a fake):

| Answer | Handling |
|---|---|
| 200 | switched; `pin` is sent as `null` when there is none |
| 401 `pin_required` | the pad opens, also for someone the list said had no PIN |
| 401 `pin_invalid` | "That PIN isn't right"; never sent twice. A switch to oneself with a PIN is a PIN check |
| 409 `pin_setup_required` | "… needs a PIN first", set in the web app |
| 409 `switch_conflict` | repeated once; still in conflict → a notice with *Try again* |
| 404 | the person is no longer listed: said, and the list is read again |
| 429 | the pad is locked for the integer `Retry-After`, with a countdown |
| 403 `not_shared_device` | explained; the list then answers the same and the picker closes |
| 401 `token_expired`, `session_stale` | the session is refreshed and the switch repeated once |
| 401 `session_revoked`, `user_suspended` | not a PIN answer: see "When the session ends" |

Whether the TV is shared is remembered on the device, so the next cold start opens on the picker at
once; if the flag was not known yet, the picker appears as soon as the server has answered.

There is no picker on a personal device (`GET /auth/switchable` answers 403 `not_shared_device`) and
on a server too old to have the route (a 404 for the path itself, as opposed to the server's own
`not_found`): the app opens on Home and the avatar leads to Settings → Profiles. Any other refusal of
the list is shown with *Try again*. That 404 is the only allowance left for older servers here.

### Watch together (rooms)

- **Starting one**: *Watch together* creates a room and shows its six-character code in large
  type, with a QR code of `<server>/room/<code>` for phones and computers. *Start* joins the room and
  opens the player; *Cancel room* closes it again. It is offered on:
  - a **film's** or an **episode's** page;
  - a **series'** page: the room is created on the series and the server opens it on the next
    episode to watch;
  - an **episode tile**: hold Select on it for its actions (*Play*, *Watch together*);
  - a **live channel**, among its actions on Live TV: a live room, with no position.
  One launcher and one dialog serve all of them (`WatchTogetherLauncher`, `WatchTogetherDialog`).
- **Joining one**: *Join a room* (rail, or the top of Search) has six slots and a picker of the code
  alphabet for the D-pad; the remote's 2–9 keys and a keyboard's letters type directly. Once the code
  is complete the room is shown (title, host, who is there) with *Join*.
- **In the room** the player works on the room's clock, not its own:
  - Play, pause and seek are sent to the room. The room's answer moves everyone, this TV included,
    so the picture changes a moment after the key press rather than at once.
  - Twice a second the player compares itself with the room (`RoomSync.decide`): more than one
    second away from the room's position it seeks; smaller differences are left alone. The server's
    clock is estimated from ping/pong, keeping the sample with the shortest round trip.
  - When this TV stalls it tells the room, which pauses for it: everyone sees
    "Waiting for <name>…". A strip at the top left shows the room code and who is there (★ host,
    … buffering) while the controls are up or the room is waiting.
  - If the host has locked the controls, a guest's play/pause/seek says
    "Only the host can control playback". The host locks and unlocks from the player menu.
  - A room on a live channel has no position: only play and pause are shared, Select plays/pauses,
    and there is no zapping, channel list, number entry or archive.
  - **Back asks "Leave the room?"** (the host can also end it for everyone). Leaving closes the
    socket normally, which frees the seat at once.
  - A dropped connection is retried with the same seat after 1, 2, 4, 8 s and then every 15 s. If
    the room dropped the seat meanwhile (20 s of silence, a member token past its two hours) or was
    out of reach for a minute, the TV joins again once with the member token it holds
    (`POST /rooms/{code}/join {member_token}`), which keeps the same seat while the room still has
    it; the old connection is dropped without a close frame for that. If the room itself is over
    (closed by the host, expired) or that join fails, a short explanation is shown and Back returns
    to where the room was opened.
- **One shared stream.** A title from a source that limits its connections is played to the whole
  room from one stream. The descriptor then carries `stream_epoch` and `start_position_ms`, and
  every `state` carries `stream_epoch` and `stream_start_ms`:
  - The stream's media time zero is `stream_start_ms` into the title, so the player belongs at
    `position_ms − stream_start_ms` (`RoomSync.playerPosition`). The scrubber, the clock and the
    progress reports are in title time, against the title's full length.
  - A seek restarts the stream on the server. When a `state` names an epoch later than the one being
    played, the TV fetches its descriptor again
    (`GET /rooms/{code}/descriptor?member_token=…&client=android_tv`, no bearer token), reloads the
    media item and lets `RoomSync` position it; "Catching up with the room…" shows meanwhile. The
    same epoch is a seek inside what is playing. A descriptor that is ahead of the last `state`
    waits for that `state` (`RoomSync.streamStep`: `Same`, `Reload`, `Wait`).
  - The stream is a playlist that grows faster than it plays, so it is always opened at an exact
    position (0 included) and held at 1× (`MediaItemMapper.pinsPosition`).
- **A member that lost its place** (a player error, a stream address that expired) asks the same
  descriptor call for its stream as it is now, twice at most before the viewer is asked; if the
  room no longer honours the seat's token (401 `room_unauthorized`, 404 `not_a_member`) it joins
  again with it. A room on a library title each member plays for themselves has no epoch: a `state`
  alone drives `RoomSync`, as before.

### Recordings

Shown to someone with `recordings.schedule` on a server that has a recordings folder:

- **Guide and Live TV now/next**: Select on a programme that is on air or still to come opens
  *Watch* (when on air) · *Record this* · *Record every new episode* (a `series` rule on the title)
  and, for a programme the server marks `is_sport`, *Record every game for <team>*: one action per
  side in the programme's `teams` (a `team` rule each). Only when the server found no sides in the
  title is there a single *Record every game for…* that asks for the team's name. The app does not
  guess: what is sport and who is playing is the server's classifier, the same one its recorder
  matches team rules with. A single recording is asked for by programme id together with the channel
  and start shown on the guide, so it survives a guide refresh. Without the permission Select
  behaves as before.
- **Marks on guide cells**: ○ will record · ● recording · ✓ recorded · ! failed or missed.
- **Recordings** (rail; needs `recordings.view`): rows *Upcoming*, *Recorded* and *Rules*. Select on
  a card opens what can be done with it (play, cancel, delete, turn a rule off or on, delete a rule)
  and, for a failed recording, the reason. A game shows its sides ("A vs B") when the server names
  them.
- **Playing a recording**: *Play* is offered for a recording that is done, and for one that failed
  but kept a file. It resolves `rec:<id>` through the same path as any pressed Play
  (`POST /playback/resolve`), and progress is reported under the same id. A refusal that is about
  the recording (403 `content_restricted` for one above the viewer's rating ceiling, 404 for one
  deleted meanwhile) is shown in the server's own words, in the usual dialog.

### Requests and notices

- A title the server marks `requestable` gets **Request** as its first button: a film or a whole
  series at once, or chosen seasons when the detail lists `request_seasons`. While a request is open
  the page says where it stands instead of offering Request again.
- Search tiles with no in-app source carry **Not available**, or the state of their request.
- **Home** shows unread notices ("your request is ready", "your request was declined") as a row above
  the hero: Select opens the title, *Mark read* beside it (or holding Select) dismisses it.
- **Requests** (rail, for someone with `requests.create`) lists *My requests*; someone who may
  approve also gets *All requests* with *Approve* and *Decline*. Someone who may only approve
  reaches the same screen from Settings → Requests, and Back returns there.

### When the server refuses a stream

A media URL (`/stream/{token}`, its playlist and segments) can be refused after playback was
resolved. Media3 reports that as an `HttpDataSource.InvalidResponseCodeException` carrying the
status and the server's error body; `StreamRefusals` reads them and the player reports the refusal
at once, without the usual load retries or a live reconnect loop:

| Refusal | What the viewer gets |
|---|---|
| 409 `user_stream_limit` | "You're already watching <title> on another device", from `active`; *Try again* |
| 403 `remote_access_denied` | "This account can only be used on the home network" |
| 403 `content_restricted` (the rating ceiling changed) | "Not available for this profile" |
| 401 `session_stale` | nothing to read: the session is refreshed and playback resolved again, once (in a room: the descriptor) |
| 401 `session_revoked`, `user_suspended` | the session is put to the server first. If it still stands, playback is resolved again once; if not, playback ends and the shell explains (see "When the session ends") |

Anything else (a provider's own 403 on a direct stream, `source_busy` on a live channel) is handled
as before.

## Module map

```
:app                    MainActivity, nav graph, left-rail shell, PlayLauncher wiring, Coil loader, manifest
:core:ui                Tokens.kt (generated) · StreamHubTheme/ThemeRepository · FocusSurface · PivotBringIntoViewSpec
                        LeftRail (RailOrder) · FocusRow/MediaRow · tiles · hero · QR (zxing) · DisclaimerGate
                        text field · PinPadDialog · ProgrammeDialog · WatchTogetherDialog
:core:data              DTOs + StreamHubApi (hand-written from contracts/openapi.yaml) · SessionStore (DataStore)
                        Ktor bearer auth w/ refresh · DeepLinkLauncher · PlaybackCoordinator · ChannelListCache
                        MeRepository (permissions) · UserSwitchRepository · SessionEvents · room/ (RoomClient
                        over WebSockets, RoomSync, RoomProtocol, RoomCode) · RoomRepository (RoomDirectory)
                        WatchTogetherLauncher · RecordingsRepository · RequestsRepository
                        ApiErrors · StreamRefusals (refused media URLs)
:core:player            StreamHubPlayer (Media3) · MediaItemMapper · VideoSurface · StreamRefusalErrors
:feature:onboarding     server URL → disclaimer gate → device-code pairing · "Who's watching?" picker
:feature:home           hero + rows; Movies/Shows as category shelves with a "See all" grid
:feature:search         debounced search, Play here / Open in app / Channels · Join a room
:feature:livetv         channel list + details pane (no auto-preview: it would use a source connection),
                        number keys, language chips, groups, favorites filter, Watch together
:feature:guide          EPG grid (30-min columns, now-line, catch-up, recording marks and Record actions)
:feature:details        title detail, resume label, source picker, seasons/episodes (hold Select: episode
                        actions), Available on, Watch together, Request
:feature:player         full-screen player overlays; watch-room mode (stream epochs, recovery); stream refusals
:feature:recordings     Upcoming / Recorded (Play) / Rules
:feature:requests       My requests / All requests
:feature:favorites      favorites by kind ("My List" in the rail)
:feature:sources        Plex PIN+QR, Jellyfin, Xtream, M3U, streaming services + Test link
:feature:settings       profiles, PIN, theme picker (live preview), languages, home rows, parental, about
```

### Design tokens

`core/ui/.../theme/Tokens.kt` is generated from `../design/tokens.json` and checked in:

```bash
./gradlew :core:ui:generateTokens   # regenerate after editing tokens.json
./gradlew :core:ui:checkTokens      # fails if Tokens.kt is stale (wired into `check`)
```

Themes switch at runtime through `ThemeRepository` (DataStore) and `LocalThemeTokens`. In the Settings
theme picker, focusing a swatch previews that theme across the whole app, and pressing OK applies it.
The applied theme is also written to the profile's `theme_id`. *Poster Adaptive* takes the Midnight
values and replaces accent and glow with `ThemeRepository.setAdaptiveAccent()`: when a poster or
landscape tile gains focus, its Coil-decoded bitmap goes through `androidx.palette` off the main thread
(`AdaptiveAccent.kt`; dominant swatch, falling back to vibrant/muted when the dominant is grey or
near-black/white, dark colours lifted ×1.8 like the web client). Results are cached per artwork URL.
Accent text is black or white, whichever contrasts more with the extracted accent.

### Remote control in the player

The mapping is `PlayerKeyMap` (a pure function, unit-tested). Everything is reachable with the
D-pad, OK and Back; media and Menu keys are shortcuts for remotes that have them.

| Key | Live | Film / catch-up / buffered history |
|---|---|---|
| Up / Down | zap (banner updates at once; the stream switches 350 ms after the last press) | show the control row |
| CH+ / CH− | zap | — |
| 0–9 | channel number; commits after 2 s or on OK | — |
| OK (short press) | mini channel list | show the control row; on the scrubber: play/pause |
| OK (hold ½ s) | audio track · subtitles · aspect ratio · sleep timer | same |
| Left / Right | archive / menu | seek ∓10 s; on the scrubber, holding speeds up |
| Back | closes what is open; otherwise exit. With a previous channel: twice within 750 ms → last channel | closes what is open; otherwise exit |
| Play/Pause, RW / FF | play/pause, ∓30 s | same |
| Menu / Settings key | same as holding OK | same |

The control row (scrubber, Play/Pause, −10 s, +10 s, Start over, Audio & subtitles) hides after 5 s
without a key press while playing and stays up while paused. A stopped stream shows a dialog with
*Try again* (which asks the server for a fresh stream) and *Back*.

Number keys work the same way on the Live TV channel list and in the guide: the typed channel is
tuned after 2 s or on OK.

In a watch room the same keys act on the room (see "Watch together"): play, pause and seek go to
everyone, a live room does not zap (Select plays/pauses instead), and Back asks before leaving.

Elsewhere, holding OK is the second action of what is focused: an episode tile's actions (Play,
Watch together), a catch-up programme in the guide, a channel's favorite on Live TV, removing an
entry from My List.

## Software audio decoding (AC-3 / E-AC-3 / DTS)

Many cheap TV sticks have no AC-3/E-AC-3/DTS decoder, so some films and IPTV streams play with no
sound. The default build does not bundle a software decoder; the player menu lists such tracks as
"(not supported on this TV)" so it is clear why they cannot be chosen.

To bundle one, build with a Gradle property:

```bash
./gradlew :app:assembleDebug -Pstreamhub.ffmpegDecoder=true
```

This adds a prebuilt FFmpeg decoder for Media3 (`io.github.anilbeesetti:nextlib-media3ext`, pinned in
`gradle/libs.versions.toml` to the build made for the pinned Media3 version). No code changes:
`SoftwareAudioRenderersFactory` finds its audio renderer by reflection and puts it ahead of the
platform decoders. Things to know before turning it on:

- **Licence.** That artifact is GPL-3.0, so an APK built with the property is a GPL-3.0 combined work.
  The property is off by default so the normal build keeps its own licence; do not distribute a
  build made with it unless you can meet the GPL's terms.
- **Audio only, and no passthrough.** Audio is decoded to PCM on the TV, so a receiver or soundbar
  gets PCM rather than the original bitstream. Video always stays on the hardware decoders.
- **Size.** The artifact carries native libraries for four ABIs and adds roughly 10 MB per ABI.
- **Keeping versions in step.** When `media3` is bumped, bump `media3FfmpegDecoder` to the build for
  the same Media3 version, or leave the property off until one exists.
- Not exercised on a device yet: the dependency and the reflective lookup are in place, the actual
  decoding has not been heard on a TV.

Alternative without the GPL artifact: build Media3's own `decoder_ffmpeg` module with the codecs
you need (see `libraries/decoder_ffmpeg/README.md` in the Media3 source for the pinned tag) and add
the resulting AAR to `core/player` as a file dependency. The stock renderer in it is also picked
up by reflection (`EXTENSION_RENDERER_MODE_PREFER`); check FFmpeg's licence for your configure flags.

## What is stubbed / known gaps

- Not run on real hardware or an emulator yet. Only the build and JVM unit tests have been verified.
  That includes everything above: the picker, rooms, recordings, requests and the handling of
  refused streams follow `contracts/openapi.yaml` and are unit-tested against fakes, not exercised
  against a running server. What most needs a TV: a shared-stream room across a seek (the reload,
  the position after it, and that playback holds 1×), the focus after the session-ended dialog, and
  the rail's scrolling with every secondary destination shown.
- A shared TV whose signed-in person is suspended cannot list or switch people (the server answers
  both calls with 401 `user_suspended`), so the picker can only explain and offer *Pair this TV
  again*.
- In a room on a series whose source does not share one stream, each member resolves the next
  episode for their own history, so two members can be given different episodes.
- "Join a room" in a two-pane (multiview) player, and *Watch together* from inside the player, are
  not offered.
- The trailer button sends `trailer:<id>` to `/playback/resolve`; the server must map that to a YouTube hand-off.
- Profile create/edit happens in the web app; the TV can switch profiles and set or remove a PIN.
