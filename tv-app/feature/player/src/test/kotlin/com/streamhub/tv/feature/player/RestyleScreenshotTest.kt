package com.streamhub.tv.feature.player

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import com.streamhub.tv.core.player.TrackOption
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The player's settings drawer and dialogs in the glass look, next to the approved controls:
 * `./gradlew :feature:player:testDebugUnitTest --tests '*RestyleScreenshotTest*' -Pstreamhub.screenshots=<dir> -Pstreamhub.screenshotArt=<dir>`.
 * Fictional titles only.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w960dp-h540dp-land-xhdpi")
class RestyleScreenshotTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    private val audio = listOf(
        TrackOption("English 5.1", "en", 0, 0, selected = true),
        TrackOption("English Stereo", "en", 0, 1, selected = false),
        TrackOption("Commentary", null, 0, 2, selected = false),
        TrackOption("Français DTS-HD", "fr", 0, 3, selected = false, supported = false),
    )
    private val text = listOf(
        TrackOption("English", "en", 1, 0, selected = false),
        TrackOption("English (SDH)", "en", 1, 1, selected = false),
    )

    private val vod = SettingsModel(
        audioTracks = audio, textTracks = text, subtitlesOff = true, autoplay = true, backMinimises = false, aspectLabel = "Fit",
        sleepOptions = PlayerViewModel.SLEEP_OPTIONS, sleepMinutes = 30, watchTogether = true,
    )

    private fun drawer(name: String, m: SettingsModel, atStreamInfo: Boolean = false) = rule.shoot(name, Behind.ClearPicture) {
        val first = remember { FocusRequester() }
        val info = remember { FocusRequester() }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { (if (atStreamInfo) info else first).requestFocus() } }
        Box(Modifier.fillMaxSize()) {
            SettingsDrawer(m, SettingsActions(), first, info, remember { FocusRequester() }, Modifier.align(Alignment.CenterEnd))
        }
    }

    /** Scrolled down to Stream info: autoplay, picture, the sleep timer chips and the actions. */
    @Test fun settingsDrawerLower() = drawer("player-settings-lower", vod, atStreamInfo = true)

    /** A film or an episode: focus on the audio track in force. */
    @Test fun settingsDrawer() = drawer("player-settings", vod)

    /** A live channel in a room the TV hosts: the room's section first. */
    @Test fun settingsDrawerRoom() = drawer(
        "player-settings-room",
        vod.copy(roomCode = "K7M2QX", roomMembers = 3, roomHost = true, autoplay = null, backMinimises = null, watchTogether = false),
    )

    /** A live channel outside a room: buffered rewind and Go Live first. */
    @Test fun settingsDrawerLive() = drawer(
        "player-settings-live",
        vod.copy(channel = true, hasBufferWindow = true, bufferedSeconds = 540, autoplay = null, backMinimises = null, canRetry = true),
    )

    @Test fun streamInfo() = rule.shoot("player-stream-info", Behind.Picture) {
        StreamInfoCard(
            listOf(
                StreamInfoLine("Source", "Library · Garden Stories"),
                StreamInfoLine("Container", "Matroska"),
                StreamInfoLine("Video", "H.264 · 1920×1080 · 23.976 fps"),
                StreamInfoLine("Audio", "E-AC-3 5.1 · English"),
                StreamInfoLine("Delivery", "Direct play"),
                StreamInfoLine("Dropped frames", "0"),
                StreamInfoLine("Buffered", "28 s"),
                StreamInfoLine("Bandwidth", "48.2 Mbit/s"),
            ),
            noDetails = false, onDismiss = {},
        )
    }

    @Test fun invite() = rule.shoot("player-invite", Behind.Picture) {
        InviteCard("K7M2QX", "https://streamhub.example/room/K7M2QX", onDismiss = {})
    }

    @Test fun leaveRoom() = rule.shoot("player-leave-room", Behind.Picture) {
        LeaveRoomCard(isHost = true, onLeave = {}, onEnd = {}, onStay = {})
    }
}
