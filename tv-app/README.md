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

## Module map

```
:app                    MainActivity, nav graph, left-rail shell, PlayLauncher wiring, Coil loader, manifest
:core:ui                Tokens.kt (generated) · StreamHubTheme/ThemeRepository · FocusSurface · PivotBringIntoViewSpec
                        LeftRail · FocusRow/MediaRow · tiles · hero · QR (zxing) · DisclaimerGate · text field
:core:data              DTOs + StreamHubApi (hand-written from contracts/openapi.yaml) · SessionStore (DataStore)
                        Ktor bearer auth w/ refresh · DeepLinkLauncher · PlaybackCoordinator · ChannelListCache
:core:player            StreamHubPlayer (Media3) · MediaItemMapper · VideoSurface
:feature:onboarding     server URL → disclaimer gate → device-code pairing
:feature:home           hero + rows; Movies/Shows as category shelves with a "See all" grid
:feature:search         debounced search, Play here / Open in app / Channels
:feature:livetv         channel list + details pane (no auto-preview: it would use a source connection),
                        number keys, language chips, groups, favorites filter
:feature:guide          EPG grid (30-min columns, now-line, catch-up)
:feature:details        title detail, resume label, source picker, seasons/episodes, Available on
:feature:player         full-screen player overlays
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
- The trailer button sends `trailer:<id>` to `/playback/resolve`; the server must map that to a YouTube hand-off.
- Profile create/edit happens in the web app; the TV can switch profiles and set or remove a PIN.
