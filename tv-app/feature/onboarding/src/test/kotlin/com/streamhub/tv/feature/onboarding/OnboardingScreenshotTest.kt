package com.streamhub.tv.feature.onboarding

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import com.streamhub.tv.core.data.model.DeviceCode
import com.streamhub.tv.core.ui.components.ScreenBackground
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The onboarding steps in the glass look:
 * `./gradlew :feature:onboarding:testDebugUnitTest --tests '*OnboardingScreenshotTest*' -Pstreamhub.screenshots=<dir>`.
 * Fictional addresses only.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w960dp-h540dp-land-xhdpi")
class OnboardingScreenshotTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    @Test fun server() = rule.shoot("onboarding-server") {
        ScreenBackground { ServerStep(OnboardingState(serverInput = "streamhub.example:8787"), onInput = {}, onSubmit = {}) }
    }

    @Test fun serverError() = rule.shoot("onboarding-server-error") {
        ScreenBackground { ServerStep(OnboardingState(serverInput = "streamhub.example", error = "No StreamHub server answered at that address."), onInput = {}, onSubmit = {}) }
    }

    @Test fun pairing() = rule.shoot("onboarding-pairing") {
        ScreenBackground {
            PairingStep(
                OnboardingState(step = OnboardingStep.Pairing, deviceCode = DeviceCode("dev-1", "K7M2QX", 600, 5), pairUrl = "https://streamhub.example/pair?code=K7M2QX"),
                onRetry = {}, onChangeServer = {},
            )
        }
    }
}
