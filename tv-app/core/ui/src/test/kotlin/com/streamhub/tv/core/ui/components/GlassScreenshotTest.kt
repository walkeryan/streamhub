package com.streamhub.tv.core.ui.components

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.unit.dp
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.Room
import com.streamhub.tv.core.data.repo.RoomSubject
import com.streamhub.tv.core.data.repo.WatchTogether
import com.streamhub.tv.core.ui.input.Arrow
import com.streamhub.tv.core.ui.input.ArrowPinState
import com.streamhub.tv.core.ui.input.PinPadState
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The shared dialogs and components in the glass look, for review against the approved player and
 * title page: `./gradlew :core:ui:testDebugUnitTest --tests '*GlassScreenshotTest*' -Pstreamhub.screenshots=<dir>`.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w960dp-h540dp-land-xhdpi")
class GlassScreenshotTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    @Test fun disclaimer() = rule.shoot("disclaimer", advanceMs = 1_200) {
        DisclaimerGate(DISCLAIMER_TEXT, DISCLAIMER_VERSION, onAccept = {}, onExit = {})
    }

    @Test fun pinKeypad() = rule.shoot("pin-keypad", Behind.Screen) {
        PinPadCard(
            title = "PIN for Alex", hint = "Enter the PIN to continue as Alex.", error = null, lockedSeconds = 0, busy = false,
            onSubmit = {}, onDismiss = {}, initial = PinPadState("123"),
        )
    }

    @Test fun pinKeypadWrong() = rule.shoot("pin-keypad-wrong", Behind.Screen) {
        PinPadCard(
            title = "PIN for Alex", hint = "Enter the PIN to continue as Alex.", error = "That PIN is not right. 2 tries left.", lockedSeconds = 0, busy = false,
            onSubmit = {}, onDismiss = {},
        )
    }

    @Test fun pinArrows() = rule.shoot("pin-arrows", Behind.Screen) {
        ArrowPinCard(
            title = "Arrow pattern for Sam", hint = "Enter your arrow pattern to continue as Sam.", length = 4, error = null, lockedSeconds = 0, busy = false,
            onSubmit = {}, initial = ArrowPinState(4).add(Arrow.entries.first()).add(Arrow.entries.last()),
        )
    }

    @Test fun pinPassword() = rule.shoot("pin-password", Behind.Screen) {
        PasswordPinCard(
            title = "Password for Jordan", hint = "Enter the password to continue as Jordan.", error = "That password is not right.", lockedSeconds = 0, busy = false,
            onSubmit = {}, onDismiss = {}, initial = "garden",
        )
    }

    private val room = Room(code = "K7M2QX")

    @Test fun watchTogether() = rule.shoot("watch-together", Behind.Picture) {
        WatchTogetherCard(
            WatchTogether("Garden Stories · S2 · E1", RoomSubject.Title, WatchTogether.Phase.Ready(room, "https://streamhub.example/room/K7M2QX")),
            onStart = {}, onRetry = {}, onCancel = {},
        )
    }

    @Test fun watchTogetherFailed() = rule.shoot("watch-together-failed", Behind.Picture) {
        WatchTogetherCard(
            WatchTogether("Garden Stories · S2 · E1", RoomSubject.Title, WatchTogether.Phase.Failed(UserError("Could not open a room", "The server did not answer. Check that it is running, then try again.", canRetry = true))),
            onStart = {}, onRetry = {}, onCancel = {},
        )
    }

    @Test fun choiceList() = rule.shoot("picker-subtitles", Behind.Picture) {
        ChoiceListCard(
            "Subtitles",
            listOf(
                ListChoice("off", "Off"),
                ListChoice("0", "English"),
                ListChoice("1", "English (SDH)"),
                ListChoice("2", "Español"),
                ListChoice("3", "Français (not supported on this TV)", enabled = false),
            ),
            selectedKey = "0", onChoose = {},
        )
    }

    @Test fun error() = rule.shoot("error", Behind.Screen) {
        ErrorCard(UserError("Playback stopped", "The stream ended unexpectedly. Try again, or go back and pick another source.", canRetry = true), onRetry = {}, onDismiss = {}, dismissLabel = "Back")
    }

    /** Buttons, chips, a field and menu rows, with focus on the secondary button. */
    @Test fun components() = rule.shoot("components", Behind.Screen) {
        val focus = remember { FocusRequester() }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { focus.requestFocus() } }
        GlassPanel(Modifier.width(820.dp)) {
            DialogTitle("Components")
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                ShButton("Play", {}, icon = GlassIcons.Play, style = ButtonStyle.Primary)
                ShButton("Focused", {}, Modifier.focusRequester(focus))
                ShButton("Secondary", {})
                ShButton("Sign out", {}, style = ButtonStyle.Danger)
                ShButton("Disabled", {}, enabled = false)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                ShChip("Off", selected = false, onClick = {})
                ShChip("30 min", selected = true, onClick = {})
                ShChip("60 min", selected = false, onClick = {})
                ShChip("Unavailable", selected = false, onClick = {}, enabled = false)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                ShTextField("streamhub.example:8787", {}, "Server address", Modifier.width(360.dp))
                Column(Modifier.width(360.dp)) {
                    GlassRow("English 5.1", {}, Modifier.fillMaxWidth(), checkable = true, checked = true)
                    GlassRow("Aspect", {}, Modifier.fillMaxWidth(), icon = GlassIcons.Picture, detail = "Fit")
                    GlassRow("Autoplay next episode", {}, Modifier.fillMaxWidth(), icon = GlassIcons.Next, trailing = { OnOffPill(true) })
                }
            }
            Row(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                GlassButton(GlassIcons.Audio, "Audio", {})
                GlassButton(GlassIcons.Subtitles, "Subtitles", {})
                GlassButton(GlassIcons.Pause, "Pause", {}, primary = true, size = 58.dp)
                GlassButton(GlassIcons.People, "Watch together", {})
                GlassButton(GlassIcons.Settings, "Settings", {})
            }
        }
    }

    /** The same pad on Daylight and High Contrast: the glass works out per theme. */
    @Test fun pinKeypadDaylight() = rule.shoot("pin-keypad-daylight", Behind.Screen, themeId = "light") {
        PinPadCard("PIN for Alex", "Enter the PIN to continue as Alex.", null, 0, false, {}, {}, initial = PinPadState("12"))
    }

    @Test fun pinKeypadContrast() = rule.shoot("pin-keypad-contrast", Behind.Screen, themeId = "contrast") {
        PinPadCard("PIN for Alex", "Enter the PIN to continue as Alex.", null, 0, false, {}, {}, initial = PinPadState("12"))
    }
}
