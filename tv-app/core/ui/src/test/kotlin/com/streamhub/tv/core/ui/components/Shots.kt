package com.streamhub.tv.core.ui.components

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.activity.ComponentActivity
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.input.InputMode
import androidx.compose.ui.platform.LocalInputModeManager
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalInspectionMode
import androidx.compose.ui.test.junit4.AndroidComposeTestRule
import androidx.test.ext.junit.rules.ActivityScenarioRule
import com.streamhub.tv.core.ui.theme.StreamHubTheme
import com.streamhub.tv.core.ui.theme.ThemeRepository
import com.streamhub.tv.core.ui.theme.Tokens
import org.junit.Assume.assumeTrue
import java.io.File

/** What sits behind a dialog in a screenshot: a native screen, or the player's picture (both dimmed as a dialog dims them). */
enum class Behind { Nothing, Screen, Picture }

/**
 * Renders [content] at the TV's 1920×1080 (960×540 dp at xhdpi) into `<streamhub.screenshots>/restyle-tv-<name>.png`.
 * Runs only when -Pstreamhub.screenshots is given. Fictional data only.
 */
fun AndroidComposeTestRule<ActivityScenarioRule<ComponentActivity>, ComponentActivity>.shoot(
    name: String,
    behind: Behind = Behind.Nothing,
    themeId: String = ThemeRepository.DEFAULT_THEME,
    advanceMs: Long = 1_000,
    content: @Composable () -> Unit,
) {
    val out = System.getProperty("streamhub.screenshots").orEmpty()
    val art = System.getProperty("streamhub.screenshotArt").orEmpty()
    assumeTrue("screenshots only when -Pstreamhub.screenshots is given", out.isNotBlank())
    val backdrop = art.takeIf { it.isNotBlank() }?.let { File(it, "backdrop.jpg") }?.takeIf { it.exists() }?.let { BitmapFactory.decodeFile(it.path) }
    // A TV is never in touch mode; screens that ask for focus on their first frame need that from the start.
    androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().setInTouchMode(false)
    setContent {
        // A TV has no touch mode: D-pad focus (and so the focus ring) only shows in keyboard input mode.
        val inputMode = LocalInputModeManager.current
        LaunchedEffect(Unit) { inputMode.requestInputMode(InputMode.Keyboard) }
        CompositionLocalProvider(LocalInspectionMode provides true) {
            StreamHubTheme(tokens = Tokens.themes.getValue(themeId)) {
                Box(Modifier.fillMaxSize()) {
                    when (behind) {
                        Behind.Nothing -> Unit
                        Behind.Screen -> ScreenBackground { Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.55f))) }
                        Behind.Picture -> {
                            Box(Modifier.fillMaxSize().background(Color(0xFF203020)))
                            backdrop?.let { Image(it.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop) }
                            Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.55f)))
                        }
                    }
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { content() }
                }
            }
        }
    }
    waitForIdle()
    mainClock.advanceTimeBy(advanceMs)
    waitForIdle()
    val view = activity.window.decorView
    val image = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
    view.draw(android.graphics.Canvas(image))
    File(out).mkdirs()
    File(out, "restyle-tv-$name.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }
}
