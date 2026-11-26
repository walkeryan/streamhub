package com.streamhub.tv.core.player

import android.view.ViewGroup
import androidx.annotation.OptIn
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.CaptionStyleCompat
import androidx.media3.ui.PlayerView

data class CaptionAppearance(val size: String, val yellow: Boolean, val background: String)

private class SurfaceState(val awake: Player.Listener) {
    var captions: CaptionAppearance? = null
    var styled = false
}

enum class AspectMode(val label: String, val resizeMode: Int) {
    FIT("Fit", AspectRatioFrameLayout.RESIZE_MODE_FIT),
    FILL("Stretch", AspectRatioFrameLayout.RESIZE_MODE_FILL),
    ZOOM("Zoom", AspectRatioFrameLayout.RESIZE_MODE_ZOOM),
    ;

    fun next(): AspectMode = entries[(ordinal + 1) % entries.size]
}

/**
 * PlayerView with built-in controller disabled (our Compose overlays handle D-pad). Not focusable.
 * It keeps the screen awake while the picture plays and lets it sleep when paused or stopped.
 */
@OptIn(UnstableApi::class)
@Composable
fun VideoSurface(player: StreamHubPlayer, modifier: Modifier = Modifier, aspect: AspectMode = AspectMode.FIT, captions: CaptionAppearance? = null) {
    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            PlayerView(ctx).apply {
                layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                useController = false
                isFocusable = false
                isFocusableInTouchMode = false
                keepScreenOn = player.exoPlayer.isPlaying
                setKeepContentOnPlayerReset(true)
                setShutterBackgroundColor(android.graphics.Color.BLACK)
                this.player = player.exoPlayer
                val awake = object : Player.Listener {
                    override fun onIsPlayingChanged(isPlaying: Boolean) { keepScreenOn = isPlaying }
                }
                player.exoPlayer.addListener(awake)
                tag = SurfaceState(awake)
            }
        },
        update = {
            it.player = player.exoPlayer
            it.resizeMode = aspect.resizeMode
            val state = it.tag as SurfaceState
            if (!state.styled || state.captions != captions) it.subtitleView?.apply {
                state.styled = true
                state.captions = captions
                if (captions == null) {
                    setUserDefaultStyle()
                    setUserDefaultTextSize()
                    setApplyEmbeddedStyles(true)
                } else {
                    setApplyEmbeddedStyles(false)
                    val text = if (captions.yellow) android.graphics.Color.YELLOW else android.graphics.Color.WHITE
                    val background = when (captions.background) {
                        "solid" -> android.graphics.Color.BLACK
                        "clear" -> android.graphics.Color.TRANSPARENT
                        else -> 0xB8000000.toInt()
                    }
                    setStyle(CaptionStyleCompat(text, background, android.graphics.Color.TRANSPARENT,
                        CaptionStyleCompat.EDGE_TYPE_OUTLINE, android.graphics.Color.BLACK, null))
                    setFractionalTextSize(when (captions.size) { "small" -> .038f; "large" -> .072f; else -> .0533f })
                }
            }
        },
        onRelease = {
            (it.tag as? SurfaceState)?.awake?.let(player.exoPlayer::removeListener)
            it.keepScreenOn = false
            it.player = null
        },
    )
}
