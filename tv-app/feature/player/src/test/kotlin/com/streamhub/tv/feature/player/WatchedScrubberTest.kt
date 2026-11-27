package com.streamhub.tv.feature.player

import androidx.activity.ComponentActivity
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalInputModeManager
import androidx.compose.ui.input.InputMode
import androidx.compose.foundation.layout.Column
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import com.streamhub.tv.core.ui.theme.StreamHubTheme
import com.streamhub.tv.core.ui.theme.Tokens
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w960dp-h540dp-land-xhdpi")
class WatchedScrubberTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    @Test fun arrowsPreviewThenSelectCommitsOnceAndTheRightEdgeGoesLive() {
        val start = Instant.parse("2026-10-14T08:00:00Z")
        val range = WatchedTimeline(start, start.plusSeconds(120))
        val committed = mutableListOf<Instant?>()
        rule.setContent {
            StreamHubTheme(tokens = Tokens.themes.getValue("midnight")) {
                var cursor by remember { mutableStateOf(range.end) }
                val focus = remember { FocusRequester() }
                val input = LocalInputModeManager.current
                Column {
                    WatchedScrubber(range, cursor, focus, true, { cursor = range.move(cursor, it) },
                        { committed.add(range.seekAt(cursor)) })
                }
                LaunchedEffect(Unit) {
                    input.requestInputMode(InputMode.Keyboard)
                    withFrameNanos { }
                    focus.requestFocus()
                }
            }
        }
        val root = rule.onRoot()
        rule.onNode(hasText("Live") and hasClickAction()).assertIsFocused()
        root.performKeyInput { pressKey(Key.DirectionLeft) }
        rule.onNodeWithText("0:10 behind live").assertIsFocused()
        rule.runOnIdle { assertEquals(emptyList<Instant?>(), committed) }
        root.performKeyInput { pressKey(Key.DirectionCenter) }
        root.performKeyInput { pressKey(Key.DirectionRight) }
        root.performKeyInput { pressKey(Key.DirectionCenter) }
        rule.runOnIdle { assertEquals(listOf(range.end.minusSeconds(10), null), committed) }
    }
}
