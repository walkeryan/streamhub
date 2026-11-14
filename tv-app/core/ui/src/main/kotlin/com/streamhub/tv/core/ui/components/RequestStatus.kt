package com.streamhub.tv.core.ui.components

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.repo.RequestBar
import com.streamhub.tv.core.data.repo.RequestStatusModel
import com.streamhub.tv.core.data.repo.RequestTone
import com.streamhub.tv.core.ui.theme.LocalReducedMotion
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** A request's phase as a chip. Its words carry the meaning; the mark and the colour only repeat it. */
@Composable
fun RequestChip(status: RequestStatusModel, modifier: Modifier = Modifier, compact: Boolean = false) {
    val t = LocalThemeTokens.current
    val ink = when (status.tone) { RequestTone.Ok -> t.success; RequestTone.Warn -> t.danger; RequestTone.Plain -> t.text }
    val mark = when (status.tone) { RequestTone.Ok -> "✓ "; RequestTone.Warn -> "! "; RequestTone.Plain -> "" }
    Text(
        mark + status.label,
        color = ink, maxLines = 1, overflow = TextOverflow.Ellipsis,
        style = if (compact) MaterialTheme.typography.labelSmall else MaterialTheme.typography.labelLarge,
        modifier = modifier.background(t.overlay, RoundedCornerShape(if (compact) 4.dp else Tokens.Radius.Sm)).padding(horizontal = if (compact) 6.dp else 10.dp, vertical = if (compact) 2.dp else 4.dp),
    )
}

/**
 * How far a download is: a rounded bar that fills. Without a percentage (the sizes are not known)
 * it only shows that something is moving, and stands still when motion is reduced.
 */
@Composable
fun RequestProgressBar(bar: RequestBar, modifier: Modifier = Modifier, height: Dp = 8.dp) {
    val t = LocalThemeTokens.current
    val shape = RoundedCornerShape(Tokens.Radius.Pill)
    val fraction = bar.fraction
    BoxWithConstraints(modifier.fillMaxWidth().height(height).clip(shape).background(t.text.copy(alpha = 0.25f), shape)) {
        if (fraction != null) {
            Box(Modifier.fillMaxWidth(fraction).fillMaxHeight().background(t.accent, shape))
        } else {
            val segment = maxWidth * 0.35f
            val travel = maxWidth - segment
            val at = if (LocalReducedMotion.current) 0.5f else {
                val transition = rememberInfiniteTransition(label = "requestBar")
                val position by transition.animateFloat(0f, 1f, infiniteRepeatable(tween(1_400, easing = LinearEasing), RepeatMode.Reverse), label = "requestBarPosition")
                position
            }
            Box(Modifier.offset(x = travel * at).width(segment).fillMaxHeight().background(t.accent, shape))
        }
    }
}

/**
 * A request's status wherever a request is shown in full (a Requests row, its dialog, the title
 * page): the phase chip, what the server adds beside it, and while downloading the bar with the
 * percentage and "about N min left".
 */
@Composable
fun RequestStatus(status: RequestStatusModel, modifier: Modifier = Modifier, barWidth: Dp = 320.dp) {
    val t = LocalThemeTokens.current
    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
            RequestChip(status)
            status.secondary?.let { Text(it, color = t.textMuted, style = MaterialTheme.typography.labelMedium, maxLines = 2, overflow = TextOverflow.Ellipsis) }
        }
        status.bar?.let { bar ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                RequestProgressBar(bar, Modifier.width(barWidth))
                status.caption?.let { Text(it, color = t.textMuted, style = MaterialTheme.typography.labelMedium, maxLines = 1) }
            }
        }
    }
}
