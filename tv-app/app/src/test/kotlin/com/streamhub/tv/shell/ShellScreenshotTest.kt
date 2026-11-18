package com.streamhub.tv.shell

import android.app.Application
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import com.streamhub.tv.core.data.repo.UpdateInfo
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The shell's native screens in the glass look:
 * `./gradlew :app:testDebugUnitTest --tests '*ShellScreenshotTest*' -Pstreamhub.screenshots=<dir>`.
 * A plain Application stands in for the Hilt one; the screens are rendered stateless. Fictional addresses only.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w960dp-h540dp-land-xhdpi", application = Application::class)
class ShellScreenshotTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    private val offer = UpdateInfo(21, "1.4.0", "https://streamhub.example/app/tv.apk", "0".repeat(64))

    @Test fun unreachable() = rule.shoot("unreachable") {
        UnreachableScreen("http://streamhub.example:8787", onRetry = {}, onChangeServer = {}, onSettings = {})
    }

    @Test fun tvSettings() = rule.shoot("tv-settings") {
        TvSettingsContent(
            TvSettingsState(serverUrl = "http://streamhub.example:8787", update = offer, updateStatus = "StreamHub 1.4.0 is available."),
            appVersion = "1.3.1", onBack = {}, onChangeServer = {}, onSignOut = {}, onInstall = {}, onCheck = {},
        )
    }

    @Test fun updatePrompt() = rule.shoot("update-prompt", Behind.Screen) {
        UpdatePromptCard(UpdatePromptState(offer = offer), appVersion = "1.3.1", onInstall = {}, onLater = {})
    }
}
