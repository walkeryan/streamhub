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
./gradlew test                                  # JVM unit tests (contract DTOs, MIME mapping, row ordering)
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
:feature:home           hero + rows; Movies/Shows browse grid
:feature:search         debounced search, Play here / Open in app / Channels
:feature:livetv         channel list + 600 ms-dwell preview, language chips, groups, favorites filter
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
values and replaces accent and glow with `ThemeRepository.setAdaptiveAccent()`.

### Remote control in the player

| Key | Live | VOD |
|---|---|---|
| Up / Down, CH+ / CH− | zap (OSD updates immediately; stream switches 350 ms after the last press) | show OSD |
| 0–9 | channel number; commits after 2 s or on OK | — |
| OK (short press) | mini channel list | play/pause |
| OK (hold ½ s) | audio track · subtitles · aspect ratio · sleep timer | same |
| Back | exit; pressed twice within 500 ms → last channel | exit |
| Left / Right, RW / FF | — | seek ±10 s / ±30 s |
| Menu / Settings key (if the remote has one) | same as holding OK | same |

## FFmpeg decoder extension (AC-3 / E-AC-3 / DTS / MP2 audio)

Many cheap TV sticks have no AC-3/E-AC-3 decoder, so IPTV streams play with no sound. Media3's
FFmpeg audio extension fixes this, but **it is not published to Maven**: you have to build it yourself.

```bash
git clone --depth 1 --branch 1.11.1 https://github.com/androidx/media.git
cd media
export FFMPEG_MODULE_PATH="$(pwd)/libraries/decoder_ffmpeg/src/main"
export NDK_PATH=$ANDROID_HOME/ndk/<version>          # sdkmanager "ndk;27.x"
export HOST_PLATFORM="linux-x86_64"
# Use the FFmpeg release branch named in libraries/decoder_ffmpeg/README.md for this Media3 tag
cd "${FFMPEG_MODULE_PATH}/jni" && git clone https://git.ffmpeg.org/ffmpeg.git ffmpeg --branch release/<x.y> --depth 1
./build_ffmpeg.sh "${FFMPEG_MODULE_PATH}" "${NDK_PATH}" "${HOST_PLATFORM}" 21 ac3 eac3 dca mp3 aac mp2 truehd
cd ../../../../.. && ./gradlew :lib-decoder-ffmpeg:assembleRelease
cp libraries/decoder_ffmpeg/buildout/outputs/aar/lib-decoder-ffmpeg-release.aar \
   <streamhub>/tv-app/core/player/libs/
```

Then uncomment `implementation(files("libs/lib-decoder-ffmpeg-release.aar"))` in
`core/player/build.gradle.kts`. No code change is needed: `StreamHubPlayer` already sets
`EXTENSION_RENDERER_MODE_PREFER`, which picks up `FfmpegAudioRenderer` by reflection when it's present.
FFmpeg is LGPL/GPL depending on the configure flags, so check the licence before distributing.

## What is stubbed / known gaps

See the "Status" section of the hand-off report. In short:

- Not run on real hardware or an emulator yet. Only the build and JVM unit tests have been verified.
- Poster Adaptive dominant-colour extraction is not implemented yet (the hook exists; it falls back to Midnight accents).
- The trailer button sends `trailer:<id>` to `/playback/resolve`; the server must map that to a YouTube hand-off.
- Profile create/edit happens in the web app; the TV can switch profiles and set or remove a PIN.
