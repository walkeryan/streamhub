package com.streamhub.tv.feature.player

import androidx.activity.ComponentActivity
import androidx.compose.runtime.remember
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import com.streamhub.tv.core.ui.theme.StreamHubTheme
import com.streamhub.tv.core.ui.theme.Tokens
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w960dp-h540dp-land-xhdpi")
class PlayerShortcutHelpTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    @Test fun filmGuideExplainsSeekAndReturnsFocusToItsMenuAction() {
        rule.setContent {
            StreamHubTheme(tokens = Tokens.themes.getValue("midnight")) {
                SettingsDrawer(
                    SettingsModel(aspectLabel = "Fit"), SettingsActions(),
                    remember { FocusRequester() }, remember { FocusRequester() }, remember { FocusRequester() },
                )
            }
        }
        val opener = rule.onNodeWithText("Controls and shortcuts")
        opener.performScrollTo().performClick()
        rule.onNodeWithText("Left or Right skips 10 seconds", substring = true).assertIsDisplayed()
        rule.onNodeWithText("Close").assertIsFocused().performClick()
        opener.assertIsFocused()
    }
}
