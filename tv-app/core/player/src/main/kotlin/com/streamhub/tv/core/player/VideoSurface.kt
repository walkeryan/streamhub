package com.streamhub.tv.core.player

import android.view.ViewGroup
import androidx.annotation.OptIn
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView

enum class AspectMode(val label: String, val resizeMode: Int) {
    FIT("Fit", AspectRatioFrameLayout.RESIZE_MODE_FIT),
    FILL("Stretch", AspectRatioFrameLayout.RESIZE_MODE_FILL),
    ZOOM("Zoom", AspectRatioFrameLayout.RESIZE_MODE_ZOOM),
    ;

    fun next(): AspectMode = entries[(ordinal + 1) % entries.size]
}

/** PlayerView with built-in controller disabled (our Compose overlays handle D-pad). Not focusable. */
@OptIn(UnstableApi::class)
@Composable
fun VideoSurface(player: StreamHubPlayer, modifier: Modifier = Modifier, aspect: AspectMode = AspectMode.FIT) {
    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            PlayerView(ctx).apply {
                layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                useController = false
                isFocusable = false
                isFocusableInTouchMode = false
                setKeepContentOnPlayerReset(true)
                setShutterBackgroundColor(android.graphics.Color.BLACK)
                this.player = player.exoPlayer
            }
        },
        update = {
            it.player = player.exoPlayer
            it.resizeMode = aspect.resizeMode
        },
        onRelease = { it.player = null },
    )
}
