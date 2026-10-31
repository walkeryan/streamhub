package com.streamhub.tv.core.ui.theme

import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdaptiveAccentTest {
    private fun contrast(a: Color, b: Color): Double {
        fun l(c: Color) = relativeLuminance((c.red * 255).toInt(), (c.green * 255).toInt(), (c.blue * 255).toInt())
        val (hi, lo) = listOf(l(a), l(b)).sortedDescending()
        return (hi + 0.05) / (lo + 0.05)
    }

    @Test fun accentTextAlwaysMeetsAa() {
        // Same sweep as the web fix: every 17th step of the sRGB cube.
        var worst = Double.MAX_VALUE
        for (r in 0..255 step 17) for (g in 0..255 step 17) for (b in 0..255 step 17) {
            val accent = Color(r, g, b)
            worst = minOf(worst, contrast(accent, accentTextFor(accent)))
        }
        assertTrue("worst case $worst", worst >= 4.5)
    }

    @Test fun picksWhiteOnDarkAndBlackOnLight() {
        assertEquals(Color.White, accentTextFor(Color(0xFF1A237E)))
        assertEquals(Color.Black, accentTextFor(Color(0xFFFFD54F)))
    }

    @Test fun darkColoursAreLifted() {
        val lifted = liftForDarkSurface(0xFF402010.toInt())
        assertEquals(0x73, (lifted shr 16) and 0xFF) // 0x40 * 1.8 = 115
        assertEquals(0xFF, (lifted ushr 24))
        // Bright colours are untouched.
        assertEquals(0xFFE8A33D.toInt(), liftForDarkSurface(0xFFE8A33D.toInt()))
    }

    @Test fun greysAndExtremesAreNotAccentLike() {
        assertFalse(isAccentLike(floatArrayOf(0f, 0.05f, 0.5f)))
        assertFalse(isAccentLike(floatArrayOf(200f, 0.8f, 0.05f)))
        assertFalse(isAccentLike(floatArrayOf(200f, 0.8f, 0.95f)))
        assertTrue(isAccentLike(floatArrayOf(30f, 0.7f, 0.55f)))
    }
}
