package com.streamhub.tv.core.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit

@Immutable
data class TypeToken(val size: TextUnit, val weight: FontWeight, val lineHeight: TextUnit)

/** One theme from design/tokens.json `themes.*` (inheritance already flattened by the generator). */
@Immutable
data class ThemeTokens(
    val id: String,
    val name: String,
    val bg: Color,
    val surface: Color,
    val surfaceAlt: Color,
    val overlay: Color,
    val text: Color,
    val textMuted: Color,
    val accent: Color,
    val accentText: Color,
    val focusRing: Color,
    val focusGlow: Color,
    val danger: Color,
    val success: Color,
    val scrimTop: Color,
    val scrimBottom: Color,
    val focusScale: Float,
    val focusBorder: Dp,
    /** Poster Adaptive: accent/focus_glow are replaced at runtime by the focused poster's dominant colour. */
    val dynamicAccent: Boolean,
    val isLight: Boolean,
)
