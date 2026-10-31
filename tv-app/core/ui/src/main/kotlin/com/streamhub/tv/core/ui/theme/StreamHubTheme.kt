package com.streamhub.tv.core.ui.theme

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.tv.material3.ColorScheme
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Shapes
import androidx.tv.material3.Typography
import androidx.tv.material3.darkColorScheme
import androidx.tv.material3.lightColorScheme
import androidx.compose.foundation.shape.RoundedCornerShape

/** Raw tokens of the active theme, for anything tv-material's ColorScheme has no slot for. */
val LocalThemeTokens = staticCompositionLocalOf { Tokens.themes.getValue(ThemeRepository.DEFAULT_THEME) }

/** Honour "reduced motion" (BRIEF UAT #7): when true, animations collapse to snaps / short fades. */
val LocalReducedMotion = staticCompositionLocalOf { false }

fun ThemeTokens.toColorScheme(): ColorScheme {
    val base = if (isLight) lightColorScheme() else darkColorScheme()
    return base.copy(
        primary = accent,
        onPrimary = accentText,
        primaryContainer = accent,
        onPrimaryContainer = accentText,
        secondary = accent,
        onSecondary = accentText,
        tertiary = success,
        background = bg,
        onBackground = text,
        surface = surface,
        onSurface = text,
        surfaceVariant = surfaceAlt,
        onSurfaceVariant = textMuted,
        inverseSurface = text,
        inverseOnSurface = bg,
        error = danger,
        onError = accentText,
        border = focusRing,
        borderVariant = textMuted,
        scrim = overlay,
    )
}

private fun style(t: TypeToken) = TextStyle(
    fontFamily = FontFamily.SansSerif, // Inter isn't bundled; tokens list Roboto/system as fallback.
    fontSize = t.size,
    fontWeight = t.weight,
    lineHeight = t.lineHeight,
)

val StreamHubTypography = Typography(
    displayLarge = style(Tokens.Type.Display),
    displayMedium = style(Tokens.Type.Display),
    displaySmall = style(Tokens.Type.Title),
    headlineLarge = style(Tokens.Type.Title),
    headlineMedium = style(Tokens.Type.Title),
    headlineSmall = style(Tokens.Type.Heading),
    titleLarge = style(Tokens.Type.Heading),
    titleMedium = style(Tokens.Type.Heading),
    titleSmall = style(Tokens.Type.Body).copy(fontWeight = Tokens.Type.Heading.weight),
    bodyLarge = style(Tokens.Type.Body),
    bodyMedium = style(Tokens.Type.Body),
    bodySmall = style(Tokens.Type.Label).copy(fontWeight = Tokens.Type.MinWeight),
    labelLarge = style(Tokens.Type.Body).copy(fontWeight = Tokens.Type.Label.weight),
    labelMedium = style(Tokens.Type.Label),
    labelSmall = style(Tokens.Type.Label),
)

val StreamHubShapes = Shapes(
    extraSmall = RoundedCornerShape(Tokens.Radius.Sm),
    small = RoundedCornerShape(Tokens.Radius.Sm),
    medium = RoundedCornerShape(Tokens.Radius.Md),
    large = RoundedCornerShape(Tokens.Radius.Lg),
    extraLarge = RoundedCornerShape(Tokens.Radius.Lg),
)

/**
 * App theme. [tokens] comes from ThemeRepository (selected or previewed theme). Colour changes
 * animate (base motion) so a live theme switch doesn't flash.
 */
@Composable
fun StreamHubTheme(
    tokens: ThemeTokens,
    adaptiveAccent: Color? = null,
    reducedMotion: Boolean = false,
    content: @Composable () -> Unit,
) {
    val effective = if (tokens.dynamicAccent && adaptiveAccent != null) {
        tokens.copy(accent = adaptiveAccent, focusGlow = adaptiveAccent, accentText = accentTextFor(adaptiveAccent))
    } else {
        tokens
    }
    val dur = if (reducedMotion) 0 else Tokens.Motion.SLOW_MS
    val bg by animateColorAsState(effective.bg, tween(dur), label = "bg")
    val surface by animateColorAsState(effective.surface, tween(dur), label = "surface")
    val accent by animateColorAsState(effective.accent, tween(dur), label = "accent")
    val text by animateColorAsState(effective.text, tween(dur), label = "text")
    val animated = effective.copy(bg = bg, surface = surface, accent = accent, text = text)

    CompositionLocalProvider(
        LocalThemeTokens provides animated,
        LocalReducedMotion provides reducedMotion,
    ) {
        MaterialTheme(
            colorScheme = animated.toColorScheme(),
            typography = StreamHubTypography,
            shapes = StreamHubShapes,
            content = content,
        )
    }
}
