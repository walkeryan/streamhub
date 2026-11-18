package com.streamhub.tv.core.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp

/**
 * The glass look the owner approved for the player and the title page (mockups 1-episode-page.png,
 * 3-player.png, v2.html), worked out for the active theme. The mockup's colours are Midnight's:
 * the page #090d1a, menus rgba(24,30,52,.96), glass translucent white with a light border and an
 * inner highlight, text #f3f5fc / #a7b0cf / #7d86a8. The other themes keep their own colours and
 * get the same structure (Daylight's glass is a dark tint, since white on white would vanish).
 */
@Immutable
data class GlassStyle(
    /** Behind a whole native screen. */
    val backdrop: Color,
    /** Dialogs, the settings drawer, menus: nearly opaque so text over a busy picture stays legible. */
    val panel: Color,
    /** Glass controls at rest: a top-to-bottom gradient, lighter at the top. */
    val fillTop: Color,
    val fillBottom: Color,
    /** The 1 dp hairline round glass. */
    val line: Color,
    /** The inner highlight along the top edge of a glass panel. */
    val highlight: Color,
    val text: Color,
    val muted: Color,
    val dim: Color,
    /** What a focused button turns into: a solid fill and ink, with the accent ring round it. */
    val focusFill: Color,
    val focusInk: Color,
    /** The primary button's gradient, top to bottom (Midnight: the mockup's #ffc062 → #ff9a1f). */
    val accentTop: Color,
    val accentBottom: Color,
    /** The tint under a focused list row (with the accent ring). */
    val rowFocus: Color,
)

/** Ink on white and on the accent: the mockup's near-black navy. */
val GlassInk = Color(0xFF0A0E1C)

private val MidnightFamily = setOf("midnight", "adaptive")

fun ThemeTokens.glass(): GlassStyle {
    val ring = accent
    return when {
        isLight -> GlassStyle(
            backdrop = bg,
            panel = Color.White.copy(alpha = 0.97f),
            fillTop = Color.Black.copy(alpha = 0.07f),
            fillBottom = Color.Black.copy(alpha = 0.03f),
            line = Color.Black.copy(alpha = 0.14f),
            highlight = Color.White.copy(alpha = 0.8f),
            text = text,
            muted = textMuted,
            dim = textMuted.copy(alpha = 0.8f),
            focusFill = text,
            focusInk = bg,
            accentTop = lerp(accent, Color.White, 0.25f),
            accentBottom = accent,
            rowFocus = ring.copy(alpha = 0.14f),
        )
        id in MidnightFamily -> GlassStyle(
            backdrop = Color(0xFF090D1A),
            panel = Color(0xF5181E34),
            fillTop = Color.White.copy(alpha = 0.16f),
            fillBottom = Color.White.copy(alpha = 0.05f),
            line = Color.White.copy(alpha = 0.18f),
            highlight = Color.White.copy(alpha = 0.10f),
            text = Color(0xFFF3F5FC),
            muted = Color(0xFFA7B0CF),
            dim = Color(0xFF7D86A8),
            focusFill = Color.White,
            focusInk = GlassInk,
            // Midnight's own accent is the mockup's; Poster Adaptive's follows the artwork.
            accentTop = if (id == "midnight") Color(0xFFFFC062) else lerp(accent, Color.White, 0.3f),
            accentBottom = if (id == "midnight") Color(0xFFFF9A1F) else accent,
            rowFocus = ring.copy(alpha = 0.18f),
        )
        else -> GlassStyle(
            backdrop = bg,
            panel = lerp(surface, Color.Black, 0.1f).copy(alpha = 0.97f),
            fillTop = Color.White.copy(alpha = 0.16f),
            fillBottom = Color.White.copy(alpha = 0.05f),
            // High Contrast wants hairlines you can actually see.
            line = Color.White.copy(alpha = if (id == "contrast") 0.6f else 0.18f),
            highlight = Color.White.copy(alpha = 0.10f),
            text = text,
            muted = textMuted,
            dim = textMuted.copy(alpha = 0.8f),
            focusFill = Color.White,
            focusInk = Color.Black,
            accentTop = lerp(accent, Color.White, 0.3f),
            accentBottom = accent,
            rowFocus = ring.copy(alpha = 0.2f),
        )
    }
}

/** The active theme's glass. */
val glass: GlassStyle
    @Composable @ReadOnlyComposable get() = LocalThemeTokens.current.glass()
