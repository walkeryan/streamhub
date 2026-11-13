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

- **Home, Movies, Shows, Discover, Sports, Favorites, Search**: browse and play what the server's
  sources have; titles that live in another app are handed off to it.
- **Live TV and Guide**: channel list with number keys, groups and languages; EPG grid with catch-up.
- **Who's watching?** on shared TVs: pick a person, with their PIN (below).
- **Watch together**: start a room from a title's page, or join one with its six-character code (below).
- **Recordings**: record one programme, every new episode, or every game for a team; manage them (below).
- **Requests**: ask for a title no source has; see the answer on Home (below).
- **Updates**: a newer build offered by the server is downloaded, verified and handed to the installer.

### Permissions

On start, after pairing and after a switch of person the app reads `GET /auth/me` and keeps the
answer (`MeRepository`). It is read again whenever the token pair had to be exchanged, which is what
a `401 session_stale` (a role or permission was changed on the server) leads to: the tokens are
refreshed silently and the call is repeated once. Actions the signed-in person may not use are not
shown:

| Needs | Shows |
|---|---|
| `rooms.create` | *Watch together* on a title's page |
| `rooms.join` | *Join a room* in the rail and in Search |
| `recordings.schedule` | the Record actions in the Guide and on Live TV |
| `recordings.view` | the *Recordings* destination (and only when the server records) |
| `requests.create` | *Request* on a title's page; Settings → Requests |
| `requests.approve` | *All requests* with Approve / Decline |

A server without `/auth/me` (404) is treated as before permissions existed: everything is offered
except what only an administrator could do. Refusals are worded for the screen: no permission (with
what is missing), a suspended account, an account waiting for approval, and "you're already watching
… on another device" for a per-person stream limit.

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

Whether the TV is shared is remembered on the device, so the next cold start opens on the picker at
once; if the flag was not known yet, the picker appears as soon as the server has answered.

If the server has no picker (`/auth/switchable` answers 404, or `POST /auth/switch` turns out not to
be a route) or this is a personal device (403), the picker is skipped: the app opens on Home, and
the avatar leads to Settings → Profiles, which keeps its per-profile list, as before.

### Watch together (rooms)

- **Starting one**: *Watch together* on a film's or episode's page creates a room and shows its
  six-character code in large type, with a QR code of `<server>/room/<code>` for phones and
  computers. *Start* joins the room and opens the player; *Cancel room* closes it again.
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
  - A dropped connection is retried with the same seat after 1, 2, 4, 8 s and then every 15 s. If the
    server says the room is over (closed by the host, expired, seat lost) or it has been out of
    reach for a minute, a short explanation is shown and Back returns to where the room was opened.

### Recordings

Shown to someone with `recordings.schedule` on a server that has a recordings folder:

- **Guide and Live TV now/next**: Select on a programme that is on air or still to come opens
  *Watch* (when on air) · *Record this* · *Record every new episode* (a `series` rule on the title) ·
  for a sports listing, *Record every game for…* with the team prefilled from the title (a `team`
  rule). A single recording is asked for by programme id together with the channel and start shown
  on the guide, so it survives a guide refresh. Without the permission Select behaves as before.
- **Marks on guide cells**: ○ will record · ● recording · ✓ recorded · ! failed or missed.
- **Recordings** (rail; needs `recordings.view`): rows *Upcoming*, *Recorded* and *Rules*. Select on
  a card opens what can be done with it (cancel, delete, turn a rule off or on, delete a rule) and,
  for a failed recording, the reason.
- **Recorded items cannot be played yet.** Select opens a dialog with *Delete* only. The server's
  `rec:` playback is still being built; `RECORDED_PLAYBACK_ENABLED` in
  `feature/recordings/.../RecordingsViewModel.kt` switches *Play* on when it is ready.

### Requests and notices

- A title the server marks `requestable` gets **Request** as its first button: a film or a whole
  series at once, or chosen seasons when the detail lists `request_seasons`. While a request is open
  the page says where it stands instead of offering Request again.
- Search tiles with no in-app source carry **Not available**, or the state of their request.
- **Home** shows unread notices ("your request is ready", "your request was declined") as a row above
  the hero: Select opens the title, *Mark read* beside it (or holding Select) dismisses it.
- **Settings → Requests** lists *My requests*; someone who may approve also gets *All requests* with
  *Approve* and *Decline*.

## Module map

```
:app                    MainActivity, nav graph, left-rail shell, PlayLauncher wiring, Coil loader, manifest
:core:ui                Tokens.kt (generated) · StreamHubTheme/ThemeRepository · FocusSurface · PivotBringIntoViewSpec
                        LeftRail · FocusRow/MediaRow · tiles · hero · QR (zxing) · DisclaimerGate · text field
:core:data              DTOs + StreamHubApi (hand-written from contracts/openapi.yaml) · SessionStore (DataStore)
                        Ktor bearer auth w/ refresh · DeepLinkLauncher · PlaybackCoordinator · ChannelListCache
                        MeRepository (permissions) · UserSwitchRepository · room/ (RoomClient over WebSockets,
                        RoomSync, RoomProtocol, RoomCode) · RecordingsRepository · RequestsRepository
:core:player            StreamHubPlayer (Media3) · MediaItemMapper · VideoSurface
:feature:onboarding     server URL → disclaimer gate → device-code pairing · "Who's watching?" picker
:feature:home           hero + rows; Movies/Shows as category shelves with a "See all" grid
:feature:search         debounced search, Play here / Open in app / Channels · Join a room
:feature:livetv         channel list + details pane (no auto-preview: it would use a source connection),
                        number keys, language chips, groups, favorites filter
:feature:guide          EPG grid (30-min columns, now-line, catch-up, recording marks and Record actions)
:feature:details        title detail, resume label, source picker, seasons/episodes, Available on,
                        Watch together, Request
:feature:player         full-screen player overlays; watch-room mode
:feature:recordings     Upcoming / Recorded / Rules
:feature:requests       My requests / All requests
:feature:favorites      favorites by kind
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

See the "Status" section of the hand-off report. In short:

- Not run on real hardware or an emulator yet. Only the build and JVM unit tests have been verified.
  That includes everything above: the picker, rooms, recordings and requests have been built against
  `contracts/openapi.yaml` and unit-tested, not exercised against a running server.
- `GET /auth/switchable` and `POST /auth/switch` were coded to their agreed shape before the server
  had them; until it does, the picker is skipped (404).
- Recorded programmes are listed but not playable (`RECORDED_PLAYBACK_ENABLED`).
- *Watch together* is offered on films and single episodes opened as their own page, not on a
  series page or from an episode tile.
- The guide's Programme has no sport flag in the contract, so "Record every game for…" is offered
  when the title or category names a sport or league (the server's own word list); a server that
  sends `is_sport` on programmes is believed instead.
- The trailer button sends `trailer:<id>` to `/playback/resolve`; the server must map that to a YouTube hand-off.
- Profile create/edit happens in the web app; the TV can switch profiles and set or remove a PIN.
