package com.streamhub.tv.core.ui.focus

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import com.streamhub.tv.core.ui.theme.LocalReducedMotion
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** CSS `ease-out` = cubic-bezier(0, 0, 0.58, 1). */
val EaseOutCss = CubicBezierEasing(0f, 0f, 0.58f, 1f)

/**
 * Token-driven focusable surface used by every tile/button:
 *  focus → scale `focus.scale` (theme override for High Contrast), `focus.border_dp` ring in
 *  `focus_ring`, glow in `focus_glow` at `glow_alpha`, animated `focus.duration_ms` ease-out.
 * Scale is a graphicsLayer transform so focus never causes layout shift (UAT #10).
 * The ring is a shape change, not just colour (UAT #1 "never colour-only").
 */
@Composable
fun FocusSurface(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onLongClick: (() -> Unit)? = null,
    shape: Shape = RoundedCornerShape(Tokens.Radius.Md),
    containerColor: Color = LocalThemeTokens.current.surface,
    focusedContainerColor: Color = containerColor,
    scaleOnFocus: Boolean = true,
    glow: Boolean = true,
    enabled: Boolean = true,
    interactionSource: MutableInteractionSource = remember { MutableInteractionSource() },
    content: @Composable BoxScope.(focused: Boolean) -> Unit,
) {
    val t = LocalThemeTokens.current
    val focused by interactionSource.collectIsFocusedAsState()
    val reduced = LocalReducedMotion.current
    val spec = if (reduced) snap<Float>() else tween(Tokens.Focus.DURATION_MS, easing = EaseOutCss)
    val dpSpec = if (reduced) snap<Dp>() else tween(Tokens.Focus.DURATION_MS, easing = EaseOutCss)
    val scale by animateFloatAsState(if (focused && scaleOnFocus) t.focusScale else 1f, spec, label = "scale")
    val ring by animateDpAsState(if (focused) t.focusBorder else 0.dp, dpSpec, label = "ring")
    val glowElev by animateDpAsState(if (focused && glow) Tokens.Focus.GlowBlur / 2 else 0.dp, dpSpec, label = "glow")
    val bg by animateColorAsState(if (focused) focusedContainerColor else containerColor, label = "bg")
    val glowColor = t.focusGlow.copy(alpha = Tokens.Focus.GLOW_ALPHA)

    val click = if (onLongClick != null) {
        Modifier.combinedClickable(interactionSource = interactionSource, indication = null, enabled = enabled, onLongClick = onLongClick, onClick = onClick)
    } else {
        Modifier.clickable(interactionSource = interactionSource, indication = null, enabled = enabled, onClick = onClick)
    }

    Box(
        modifier
            .zIndex(if (focused) 1f else 0f)
            .graphicsLayer { scaleX = scale; scaleY = scale }
            .then(if (glowElev > 0.dp) Modifier.shadow(glowElev, shape, clip = false, ambientColor = glowColor, spotColor = glowColor) else Modifier)
            .clip(shape)
            .background(bg, shape)
            .then(click)
            .then(if (ring > 0.dp) Modifier.border(ring, t.focusRing, shape) else Modifier),
    ) {
        content(focused)
    }
}
