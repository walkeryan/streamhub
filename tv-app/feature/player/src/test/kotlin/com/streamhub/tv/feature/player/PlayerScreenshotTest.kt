package com.streamhub.tv.feature.player

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalInspectionMode
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.unit.dp
import coil3.annotation.ExperimentalCoilApi
import coil3.asImage
import coil3.compose.AsyncImagePreviewHandler
import coil3.compose.LocalAsyncImagePreviewHandler
import com.streamhub.tv.core.data.model.PlaybackMarker
import com.streamhub.tv.core.data.model.UpNext
import com.streamhub.tv.core.ui.theme.StreamHubTheme
import com.streamhub.tv.core.ui.theme.ThemeRepository
import com.streamhub.tv.core.ui.theme.Tokens
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/**
 * Renders the revamped player's overlays at the TV's 1920×1080 (960×540 dp at xhdpi) into PNGs for
 * review against the approved mockup. Runs only when asked:
 * `./gradlew :feature:player:testDebugUnitTest --tests '*PlayerScreenshotTest*' -Pstreamhub.screenshots=<dir>`
 * (`-Pstreamhub.screenshotArt=<dir>` puts backdrop.jpg / ep2.jpg / ep4.jpg from there behind them).
 * Fictional titles only.
 */
@OptIn(ExperimentalCoilApi::class)
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w960dp-h540dp-land-xhdpi")
class PlayerScreenshotTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    private val out = System.getProperty("streamhub.screenshots").orEmpty()
    private val art = System.getProperty("streamhub.screenshotArt").orEmpty()

    private fun bitmap(name: String): Bitmap? = art.takeIf { it.isNotBlank() }?.let { File(it, name) }?.takeIf { it.exists() }?.let { BitmapFactory.decodeFile(it.path) }

    private val next = UpNext("lib:demo:ep:202", "Episode 2", "S2 · E2", image = "art://ep2.jpg", durationMs = 3_000_000)

    private val model = ControlsModel(
        kicker = "Garden Stories · Season 2 · Episode 1",
        title = "Episode 1",
        positionMs = 1_902_000,
        bufferedMs = 2_300_000,
        durationMs = 3_000_000,
        seeking = false,
        thumbnailUrl = null,
        markers = listOf(PlaybackMarker("intro", 45_000, 120_000), PlaybackMarker("credits", 2_820_000, 3_000_000)),
        playing = true,
        seekable = true,
        episodeButtons = true,
        hasPrevious = true,
        hasNext = true,
        audioLabel = "English 5.1",
        subtitleLabel = "Off",
    )

    private val actions = ControlsActions({}, {}, {}, {}, {}, {}, {}, {}, {}, onWatchTogether = {}, onMinimise = {}, onSettings = {})

    private fun shoot(name: String, content: @Composable () -> Unit) {
        assumeTrue("screenshots only when -Pstreamhub.screenshots is given", out.isNotBlank())
        val backdrop = bitmap("backdrop.jpg")
        val images = mapOf("ep2" to bitmap("ep2.jpg"), "thumb" to bitmap("ep4.jpg"))
        rule.setContent {
            CompositionLocalProvider(
                LocalInspectionMode provides true,
                LocalAsyncImagePreviewHandler provides AsyncImagePreviewHandler { request ->
                    val key = if (request.data.toString().contains("ep2")) "ep2" else "thumb"
                    (images[key] ?: Bitmap.createBitmap(16, 9, Bitmap.Config.ARGB_8888).apply { eraseColor(0xFF2A3346.toInt()) }).asImage()
                },
            ) {
                StreamHubTheme(tokens = Tokens.themes.getValue(ThemeRepository.DEFAULT_THEME)) {
                    Box(Modifier.fillMaxSize().background(Color(0xFF203020))) {
                        backdrop?.let { Image(it.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop) }
                        content()
                    }
                }
            }
        }
        rule.waitForIdle()
        rule.mainClock.advanceTimeBy(1_000)
        rule.waitForIdle()
        // The window's own drawing, into a software canvas (Robolectric's native graphics).
        val view = rule.activity.window.decorView
        val image = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        view.draw(android.graphics.Canvas(image))
        File(out).mkdirs()
        File(out, "player-tv-$name.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }

    /** The controls with the Up next card (counting down) and Skip credits above the timeline, as mockup 3. */
    @Test fun controlsWithUpNextAndSkipCredits() = shoot("controls") {
        val timeline = remember { FocusRequester() }
        Box(Modifier.fillMaxSize()) {
            VodControls(model, actions, ControlsFocus.PlayPause, ControlsLinks(), timeline)
            Column(Modifier.align(Alignment.BottomEnd).padding(end = Tokens.Canvas.SafeMarginX, bottom = 185.dp), horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(14.dp)) {
                UpNextCard(UpNextCardModel(next, secondsLeft = 8, progress = 0.2f), onPlay = {})
                SkipButton("Skip credits", onClick = {})
            }
        }
    }

    /** Seeking from the remote: the scrub thumbnail and time ride above the knob. */
    @Test fun scrubbingWithThumbnail() = shoot("scrub") {
        val timeline = remember { FocusRequester() }
        VodControls(model.copy(seeking = true, thumbnailUrl = "art://thumb", positionMs = 1_902_000), actions, ControlsFocus.Timeline, ControlsLinks(), timeline)
    }

    /** Controls hidden: Skip intro alone, bottom-right, where OK takes it. */
    @Test fun skipIntroWithControlsHidden() = shoot("skip-intro") {
        Box(Modifier.fillMaxSize()) {
            SkipButton("Skip intro", onClick = {}, Modifier.align(Alignment.BottomEnd).padding(end = Tokens.Canvas.SafeMarginX, bottom = 33.dp))
        }
    }

    /** Autoplay off: the card offers Play next and has no countdown. */
    @Test fun upNextWithAutoplayOff() = shoot("upnext-manual") {
        Box(Modifier.fillMaxSize()) {
            UpNextCard(UpNextCardModel(next, secondsLeft = null, progress = 0f), onPlay = {}, Modifier.align(Alignment.BottomEnd).padding(end = Tokens.Canvas.SafeMarginX, bottom = 33.dp))
        }
    }

    @Test fun stillWatching() = shoot("still-watching") {
        StillWatchingPanel(next, onContinue = {}, onBack = {})
    }
}
