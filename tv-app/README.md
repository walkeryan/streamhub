# StreamHub for Android TV

A thin native shell around the StreamHub web app (`web-app/`), with native playback. Kotlin 2.3 ·
`android.webkit.WebView` · Compose for TV (`tv-material 1.1.0`) for the few native screens · Media3 1.11 ·
Hilt · Ktor (CIO) · DataStore. minSdk 24, target/compileSdk 36, leanback-only (no touchscreen required).

## What is web, what is native

The TV shows the paired server's web app full screen, exactly as a 1920×1080 desktop browser shows it.
Every screen a person browses (Home, Movies, Shows, Live TV and the guide, Search, Sports, My List,
Recordings, Requests, Sources, Settings, profiles, "Who's watching?", watch-room join pages) is the web
app, so every change to it reaches the TV the moment the server serves it. Native are only:

| Native | Why |
|---|---|
| Pairing: legal notice, server address, device code | How a TV signs in (`POST /auth/device`, `/auth/device/poll`), before there is a session for the web app |
| Playback (`PlayerActivity`, `feature/player`) | Smooth 4K, surround passthrough to a receiver or soundbar, formats a browser cannot play, live zapping, watch rooms |
| "Cannot reach your server" | Shown when the page fails to load: *Retry*, *Change server*, *TV settings* |
| TV settings | Server address, *Sign out of this TV*, app version, update check (`GET /app/tv`, verified download, system installer) |

### The shell (`app/…/MainActivity`, `app/…/shell/`)

- **Layout.** The WebView uses `useWideViewPort`; in shell mode the web app sets its viewport to
  `width=1920` at the scale that fits the screen (`web-app/src/shellProtocol.ts`, `shellViewport`).
  Google TV WebViews report about 960×540 CSS pixels, so without this the web app would fall into its
  tablet layout; with it `window.innerWidth` is 1920 and the root font is the web app's TV size (24 px).
- **Keys.** D-pad arrives as arrow keys, Select as Enter; digits and media keys pass through to the
  page. The hardware **Back** key calls `window.streamhubShell.back()`, the same handling as Escape and
  Backspace in a browser (close the open dialog, else go back). It answers `false` only on the home
  screen with nothing to close, and then the app is left like any TV app.
- **Lifecycle.** The activity handles configuration changes itself and keeps its WebView, so a change
  of display mode, a trip to the player or to the home screen never reloads the page. Backgrounded,
  the page's timers pause; behind the player it keeps running (it answers token requests). The WebView
  holds no wake lock; the video surface keeps the screen awake only while the picture plays.
- **Locked down.** No address bar, zoom, long-press menus, selection callouts, pop-up windows, file or
  content access, or permission grants. Only the paired server's origin (`scheme://host:port`) may load;
  any other address, scheme or external link loads nowhere (`mayLoad`). Debug builds enable WebView
  remote debugging (`chrome://inspect`); release builds do not.

### The session: one owner

The web app owns the session: it refreshes, and refresh tokens rotate on every `/auth/refresh` (a
replayed old one ends the session). The TV never keeps a second refreshing copy:

1. Pairing gives the TV a token pair. Before the web app is loaded, the shell opens a blank document of
   the server's origin (`loadDataWithBaseURL`), writes the pair into that origin's `localStorage` under
   `sh.session.v1` in the exact shape `web-app/src/store/session.ts` reads (`{access, refresh, profile}`),
   checks it is there, and only then loads the web app.
2. The TV then drops its refresh token (`SessionStore.handOverToWeb`). It keeps the server address, the
   person, the last access token it was handed and the session's identity (generation), so progress
   reports and stream leases stay bound to the right person.
3. An install of an earlier version (it has a native pair) is migrated the same way on its first start.
4. When the web app signs out (or finds no session), it calls `signedOut()` and the TV returns to its
   pairing screen, showing the reason when there is one. *Sign out of this TV* does the same from the
   native side.

### The bridge

`window.StreamHubTV` is added before the first page loads, so the web app finds it at document start.
Version 1:

| Page → TV (`window.StreamHubTV`) | |
|---|---|
| `play(json): boolean` | Start native playback. `{"v":1,"kind":"title","id",…}` with optional `source_id`, `variant_id`, `allow_unverified_audio` (only with a variant), `catchup_programme_id` or `rewind_at`, `start_position_ms`, `audio_track`; or `{"v":1,"kind":"room","room_code","member_token","allow_unverified_audio"}`. Every field is type- and shape-checked (`parseShellPlay`); unknown fields are ignored; false when refused |
| `signedOut(reason)` | The web app has no session any more |
| `capabilities(): string` | `{"bridge":1,"shell":"1.2.0","version_code":7,"native_playback":true,"hdr":[…],"video_codecs":[…],"audio_passthrough":[…]}` |
| `deliverToken(id, json)` | The answer to a token request: `{"access","profile"}`, or `null` |

| TV → page (`window.streamhubShell`) | |
|---|---|
| `back(): boolean` | One press of Back (above) |
| `token(id, rejected)` | Asks for a current access token. The web app refreshes first when its token has expired or is the one the server just refused (`rejected`), and answers through `deliverToken` with the same id |
| `playbackEnded()` | The player closed: the page reads progress on tiles again; screen and focus are where they were |

Nothing that arrives from the page is used as an address: `play()` carries only the inputs to a
resolve, and the TV resolves them itself through the API with `client=android_tv` and the web app's
access token. Token request ids are random, single-use and expire after 15 s (`WebTokenBroker`).

### Native playback

Play in the web app (a tile, a title's Play, a channel, catch-up, a recording `rec:<id>`) never starts
the web player in the shell. The web app hands the request over; the TV first takes the web app's
current token and person (`WebSessionTokens`), then resolves through `PlaybackCoordinator` exactly as
before (its own audio check and dialog when the web app had not chosen a variant, hand-off to another
TV app for sources that live there) and opens `PlayerActivity`. A watch room is joined by the web app
with `client=android_tv`; the TV takes the seat up with its member token (`RoomRepository.joinHeld`)
and the player runs in room mode as before. Back from the player returns to the WebView.

The player's API calls (resolve, progress, room descriptor, buffer leases) carry the access token the
web app handed over. On a 401 the HTTP client asks the web app for a current one and repeats the call
once (`createStreamHubHttpClient(webTokens = …)`); progress reports, which carry their token outside
the bearer plugin, do the same. The TV never calls `/auth/refresh` itself.

## Build

Requirements: JDK 17+ (21 tested) and an Android SDK with `platforms;android-36` + `build-tools;36.x`.

```bash
cd tv-app
cp local.properties.example local.properties   # set sdk.dir to your SDK path
./gradlew :app:assembleDebug                    # → app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:assembleRelease                  # minified; unsigned unless STREAMHUB_SIGNING_PROPERTIES names a signing file
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
   adb connect 192.0.2.50:5555          # accept the prompt on the TV
   adb install -r app/build/outputs/apk/debug/app-debug.apk
   adb shell am start -n com.streamhub.tv.debug/com.streamhub.tv.MainActivity
   ```

   Debug builds install as `com.streamhub.tv.debug` alongside a release build (`com.streamhub.tv`).
3. The app appears in the TV launcher's Apps row with the StreamHub banner.

## Point the app at your server

On first launch: the legal notice (docs/LEGAL.md), then the server address (e.g. `192.0.2.10:8787`;
`http://` and `:8787` are added if left out; the app checks `GET /api/v1/health`), then a 6-character
code and a QR code (`<server>/pair?code=XXXXXX`) to approve from the web app on a phone while signed in.
The web app then opens full screen. To change server: *Change server* on the "Cannot reach your server"
screen or in TV settings. Cleartext HTTP is allowed (`network_security_config.xml`) because the server
and most IPTV upstreams are on the LAN over plain HTTP.

## The native player

Everything below is unchanged from the native app and is what `PlayerActivity` shows: audio tracks
and passthrough, subtitles, aspect ratio, sleep timer, live zapping and number entry, catch-up and
buffered history, stream refusals, watch rooms (the room's clock, shared streams and their epochs, seat
recovery, "Leave the room?").

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

## Module map

```
:app                    MainActivity (WebView shell), shell/ (bridge protocol, ShellBridge, WebView controller,
                        native screens: Cannot reach your server, TV settings), PlayerActivity, manifest
:core:ui                Tokens.kt (generated) · StreamHubTheme/ThemeRepository · buttons, dialogs, DisclaimerGate
:core:data              DTOs + StreamHubApi · SessionStore (DataStore) · WebSessionTokens/WebTokenBroker (tokens
                        from the web app) · Ktor client · PlaybackCoordinator · room/ (RoomClient, RoomSync) ·
                        RoomRepository · UpdateRepository/UpdateInstaller · ApiErrors · StreamRefusals
:core:player            StreamHubPlayer (Media3) · MediaItemMapper · VideoSurface · StreamRefusalErrors
:feature:onboarding     legal notice → server address → device-code pairing
:feature:player         full-screen player overlays; watch-room mode; stream refusals
```

The native browsing screens (`feature/home`, `details`, `livetv`, `guide`, `search`, `favorites`,
`recordings`, `requests`, `settings`, `sources`) were deleted with version 1.2.0: the web app is those
screens now. Left in the code but not reachable: the native "Who's watching?" picker in
`feature/onboarding`, the two-pane `MultiviewScreen` in `feature/player`, and the rail and tile
components in `core/ui`.

## Design tokens

`core/ui/.../theme/Tokens.kt` is generated from `../design/tokens.json` and checked in:

```bash
./gradlew :core:ui:generateTokens   # regenerate after editing tokens.json
./gradlew :core:ui:checkTokens      # fails if Tokens.kt is stale (wired into `check`)
```

The native screens (pairing, the player's overlays, TV settings) use the theme last selected through
`ThemeRepository` (DataStore); the web app has its own themes, chosen in its settings.

## What is stubbed / known gaps

- Built and unit-tested only (JVM tests; the web side with Playwright against a fake bridge). The shell
  has not been run on a TV or an emulator yet. What most needs a device: the computed layout width
  (1920) on the TV's own WebView, D-pad and Select reaching the page, Back on the home screen leaving
  the app, the session seed on first start (new pairing and the upgrade from 1.1.x), and a film played
  for longer than an access token lives (the token request round trip while the player is in front).
- A frame from another origin inside the web app would see `window.StreamHubTV` too (the platform adds
  it to every frame). The web app embeds none; `play()` and `signedOut()` only act while the top page is
  the paired server's, and token answers are only taken for ids the TV issued.
- The audio check the TV makes itself (no variant chosen in the web app) uses the native dialog.
