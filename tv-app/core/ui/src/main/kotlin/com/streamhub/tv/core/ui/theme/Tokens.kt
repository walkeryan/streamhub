// GENERATED from design/tokens.json by `./gradlew :core:ui:generateTokens`. Do not edit by hand.
@file:Suppress("MagicNumber")

package com.streamhub.tv.core.ui.theme

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

object Tokens {
    object Canvas {
        const val DESIGN_WIDTH_DP = 960
        const val DESIGN_HEIGHT_DP = 540
        val SafeMarginX = 48.dp
        val SafeMarginY = 27.dp
    }

    object Radius {
        val Sm = 6.dp
        val Md = 10.dp
        val Lg = 16.dp
        val Pill = 999.dp
    }

    object Spacing {
        val Xs = 4.dp
        val Sm = 8.dp
        val Md = 16.dp
        val Lg = 24.dp
        val Xl = 32.dp
        val Xxl = 48.dp
    }

    object Type {
        const val FAMILY = "Inter, Roboto, system-ui, sans-serif"
        val MinWeight = FontWeight(400)
        val Display = TypeToken(size = 44.0.sp, weight = FontWeight(700), lineHeight = 48.4.sp)
        val Title = TypeToken(size = 28.0.sp, weight = FontWeight(600), lineHeight = 33.6.sp)
        val Heading = TypeToken(size = 20.0.sp, weight = FontWeight(600), lineHeight = 26.0.sp)
        val Body = TypeToken(size = 16.0.sp, weight = FontWeight(400), lineHeight = 24.0.sp)
        val Label = TypeToken(size = 13.0.sp, weight = FontWeight(500), lineHeight = 18.2.sp)
    }

    object Focus {
        const val SCALE = 1.08f
        val Border = 3.dp
        val GlowBlur = 24.dp
        const val GLOW_ALPHA = 0.55f
        const val DURATION_MS = 180
        const val EASING = "ease-out"
    }

    object Motion {
        const val FAST_MS = 120
        const val BASE_MS = 180
        const val SLOW_MS = 260
        const val HERO_CROSSFADE_MS = 420
    }

    object Tile {
        val PosterW = 150.dp
        val PosterH = 225.dp
        val LandscapeW = 256.dp
        val LandscapeH = 144.dp
        val ChannelW = 220.dp
        val ChannelH = 96.dp
        val Gap = 16.dp
    }

    object Rail {
        val CollapsedW = 72.dp
        val ExpandedW = 240.dp
    }

    val themes: Map<String, ThemeTokens> = linkedMapOf(
        "midnight" to ThemeTokens(
            id = "midnight",
            name = "Midnight",
            bg = Color(0xFF0B0F17),
            surface = Color(0xFF141A25),
            surfaceAlt = Color(0xFF1C2433),
            overlay = Color(0xB80B0F17),
            text = Color(0xFFF2F5F9),
            textMuted = Color(0xFF9AA6B8),
            accent = Color(0xFFE8A33D),
            accentText = Color(0xFF0B0F17),
            focusRing = Color(0xFFFFFFFF),
            focusGlow = Color(0xFFE8A33D),
            danger = Color(0xFFE5484D),
            success = Color(0xFF46A758),
            scrimTop = Color(0x000B0F17),
            scrimBottom = Color(0xEB0B0F17),
            focusScale = 1.08f,
            focusBorder = 3.dp,
            dynamicAccent = false,
            isLight = false,
        ),
        "oled" to ThemeTokens(
            id = "oled",
            name = "OLED Black",
            bg = Color(0xFF000000),
            surface = Color(0xFF0E0E0E),
            surfaceAlt = Color(0xFF181818),
            overlay = Color(0xC7000000),
            text = Color(0xFFFFFFFF),
            textMuted = Color(0xFFA0A0A0),
            accent = Color(0xFF4DA3FF),
            accentText = Color(0xFF000000),
            focusRing = Color(0xFFFFFFFF),
            focusGlow = Color(0xFF4DA3FF),
            danger = Color(0xFFFF5C5C),
            success = Color(0xFF3DD68C),
            scrimTop = Color(0x00000000),
            scrimBottom = Color(0xF0000000),
            focusScale = 1.08f,
            focusBorder = 3.dp,
            dynamicAccent = false,
            isLight = false,
        ),
        "light" to ThemeTokens(
            id = "light",
            name = "Daylight",
            bg = Color(0xFFF6F7F9),
            surface = Color(0xFFFFFFFF),
            surfaceAlt = Color(0xFFEDF0F4),
            overlay = Color(0xCCF6F7F9),
            text = Color(0xFF111827),
            textMuted = Color(0xFF4B5563),
            accent = Color(0xFF2563EB),
            accentText = Color(0xFFFFFFFF),
            focusRing = Color(0xFF111827),
            focusGlow = Color(0xFF2563EB),
            danger = Color(0xFFDC2626),
            success = Color(0xFF16A34A),
            scrimTop = Color(0x00F6F7F9),
            scrimBottom = Color(0xEBF6F7F9),
            focusScale = 1.08f,
            focusBorder = 3.dp,
            dynamicAccent = false,
            isLight = true,
        ),
        "contrast" to ThemeTokens(
            id = "contrast",
            name = "High Contrast",
            bg = Color(0xFF000000),
            surface = Color(0xFF000000),
            surfaceAlt = Color(0xFF111111),
            overlay = Color(0xE6000000),
            text = Color(0xFFFFFFFF),
            textMuted = Color(0xFFE6E6E6),
            accent = Color(0xFFFFD600),
            accentText = Color(0xFF000000),
            focusRing = Color(0xFFFFD600),
            focusGlow = Color(0xFFFFD600),
            danger = Color(0xFFFF4040),
            success = Color(0xFF40FF80),
            scrimTop = Color(0x00000000),
            scrimBottom = Color(0xFA000000),
            focusScale = 1.12f,
            focusBorder = 4.dp,
            dynamicAccent = false,
            isLight = false,
        ),
        "adaptive" to ThemeTokens(
            id = "adaptive",
            name = "Poster Adaptive",
            bg = Color(0xFF0B0F17),
            surface = Color(0xFF141A25),
            surfaceAlt = Color(0xFF1C2433),
            overlay = Color(0xB80B0F17),
            text = Color(0xFFF2F5F9),
            textMuted = Color(0xFF9AA6B8),
            accent = Color(0xFFE8A33D),
            accentText = Color(0xFF0B0F17),
            focusRing = Color(0xFFFFFFFF),
            focusGlow = Color(0xFFE8A33D),
            danger = Color(0xFFE5484D),
            success = Color(0xFF46A758),
            scrimTop = Color(0x000B0F17),
            scrimBottom = Color(0xEB0B0F17),
            focusScale = 1.08f,
            focusBorder = 3.dp,
            dynamicAccent = true,
            isLight = false,
        ),
    )
}
