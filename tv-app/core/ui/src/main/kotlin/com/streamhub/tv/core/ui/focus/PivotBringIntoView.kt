package com.streamhub.tv.core.ui.focus

import androidx.compose.foundation.gestures.BringIntoViewSpec
import androidx.compose.foundation.gestures.LocalBringIntoViewSpec
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp

/**
 * Keeps the focused child's leading edge at a fixed offset from the container's leading edge
 * (TV "pivot" scrolling): rows don't jitter as focus moves, the focused card always lands in the
 * same place. Near the end of the list the item is clamped so it stays fully visible.
 */
class PivotBringIntoViewSpec(private val leadingOffsetPx: Float) : BringIntoViewSpec {
    override fun calculateScrollDistance(offset: Float, size: Float, containerSize: Float): Float {
        val target = if (size <= containerSize && containerSize - leadingOffsetPx < size) {
            containerSize - size
        } else {
            leadingOffsetPx
        }
        return offset - target
    }
}

/** Provide a pivot spec for the scrollable(s) in [content]. Use one per LazyRow / LazyColumn. */
@Composable
fun PivotScroll(leadingOffset: Dp, content: @Composable () -> Unit) {
    val px = with(LocalDensity.current) { leadingOffset.toPx() }
    val spec = remember(px) { PivotBringIntoViewSpec(px) }
    CompositionLocalProvider(LocalBringIntoViewSpec provides spec, content = content)
}
