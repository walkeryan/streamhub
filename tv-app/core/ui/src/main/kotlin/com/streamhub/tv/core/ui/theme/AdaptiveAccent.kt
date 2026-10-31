package com.streamhub.tv.core.ui.theme

import android.graphics.Bitmap
import android.os.Build
import android.util.LruCache
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.palette.graphics.Palette
import coil3.compose.AsyncImagePainter
import coil3.toBitmap
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.pow
import kotlin.math.roundToInt

/**
 * Poster Adaptive theme: turns the focused artwork's bitmap into an accent colour and hands it to
 * [onAccent] (wired to [ThemeRepository.setAdaptiveAccent]). Extraction runs off the main thread
 * with androidx.palette and is cached per artwork URL, so re-focusing a tile is instant.
 */
class AdaptiveAccentController(
    private val scope: CoroutineScope,
    private val onAccent: (Color?) -> Unit,
) {
    private val cache = LruCache<String, Int>(128)
    private var latestKey: String? = null
    private var job: Job? = null

    fun onFocusedArtwork(key: String, bitmap: Bitmap) {
        latestKey = key
        cache.get(key)?.let { onAccent(it.toAccentOrNull()); return }
        job?.cancel()
        job = scope.launch {
            val argb = withContext(Dispatchers.Default) { runCatching { extractAccent(bitmap) }.getOrNull() }
            cache.put(key, argb ?: NO_COLOUR)
            if (latestKey == key) onAccent(argb?.let { Color(it) })
        }
    }

    private fun Int.toAccentOrNull(): Color? = if (this == NO_COLOUR) null else Color(this)

    private companion object {
        const val NO_COLOUR = 0 // fully transparent black: never a real extraction result
    }
}

/** Provided by the activity; null (no-op) in previews and tests. */
val LocalAdaptiveAccent = staticCompositionLocalOf<AdaptiveAccentController?> { null }

/**
 * Per-tile glue between Coil and [AdaptiveAccentController]. Pass [onSuccess] to `AsyncImage` and
 * call [onFocusChanged] from the tile. The decoded bitmap is captured whatever the theme, so switching
 * to Poster Adaptive works for tiles that had already loaded; it is only analysed while that theme is
 * active and the tile is focused.
 */
class AdaptiveArt internal constructor(private val key: String?) {
    internal var controller: AdaptiveAccentController? = null
    private var bitmap: Bitmap? = null
    private var focused = false

    val onSuccess: (AsyncImagePainter.State.Success) -> Unit = { s ->
        bitmap = runCatching { s.result.image.toBitmap() }.getOrNull()
        if (focused) push()
    }

    fun onFocusChanged(isFocused: Boolean) {
        focused = isFocused
        if (isFocused) push()
    }

    private fun push() {
        val c = controller ?: return
        val k = key ?: return
        val b = bitmap ?: return
        c.onFocusedArtwork(k, b)
    }
}

@Composable
fun rememberAdaptiveArt(key: String?): AdaptiveArt {
    val art = remember(key) { AdaptiveArt(key) }
    val controller = LocalAdaptiveAccent.current.takeIf { LocalThemeTokens.current.dynamicAccent }
    SideEffect { art.controller = controller }
    return art
}

/**
 * Dominant colour of [src] via androidx.palette, as an accent usable on the dark Midnight surfaces.
 * The dominant swatch wins when it is a real colour; near-black/white or grey dominants (letterboxing,
 * skies, title cards) fall back to the vibrant swatches, then muted, then the dominant anyway.
 */
internal fun extractAccent(src: Bitmap): Int? {
    if (src.isRecycled) return null
    val bmp = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && src.config == Bitmap.Config.HARDWARE) {
        src.copy(Bitmap.Config.ARGB_8888, false) ?: return null // hardware bitmaps can't be read directly
    } else {
        src
    }
    val p = Palette.from(bmp).maximumColorCount(16).resizeBitmapArea(96 * 96).generate()
    val dominant = p.dominantSwatch
    val pick = dominant?.takeIf { isAccentLike(it.hsl) }
        ?: p.vibrantSwatch ?: p.lightVibrantSwatch ?: p.darkVibrantSwatch
        ?: p.mutedSwatch ?: p.lightMutedSwatch ?: dominant
    return pick?.rgb?.let(::liftForDarkSurface)
}

/** HSL from Palette: [hue 0..360, saturation 0..1, lightness 0..1]. */
internal fun isAccentLike(hsl: FloatArray): Boolean = hsl[1] >= 0.25f && hsl[2] in 0.2f..0.85f

/** Same rule as the web client (web-app/src/lib/adaptive.ts): lift dark colours so the accent stays legible. */
internal fun liftForDarkSurface(argb: Int): Int {
    val r = (argb shr 16) and 0xFF
    val g = (argb shr 8) and 0xFF
    val b = argb and 0xFF
    val lift = if (relativeLuminance(r, g, b) < 0.18) 1.8 else 1.0
    fun ch(v: Int) = (v * lift).roundToInt().coerceAtMost(255)
    return (0xFF shl 24) or (ch(r) shl 16) or (ch(g) shl 8) or ch(b)
}

/** WCAG 2.x relative luminance of an sRGB colour (0–255 channels). */
internal fun relativeLuminance(r: Int, g: Int, b: Int): Double {
    fun f(v: Int): Double {
        val c = v / 255.0
        return if (c <= 0.03928) c / 12.92 else ((c + 0.055) / 1.055).pow(2.4)
    }
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

/**
 * Text colour for an adaptive accent: black or white, whichever contrasts more (UAT P2-9; mirrors web
 * fix ede799a — a fixed luminance threshold put white on mid-tone accents at 2.3–4.5:1). Pure black
 * keeps the worst case at ~4.6:1.
 */
fun accentTextFor(accent: Color): Color {
    val r = (accent.red * 255).roundToInt()
    val g = (accent.green * 255).roundToInt()
    val b = (accent.blue * 255).roundToInt()
    val l = relativeLuminance(r, g, b)
    val onBlack = (l + 0.05) / 0.05
    val onWhite = 1.05 / (l + 0.05)
    return if (onBlack >= onWhite) Color.Black else Color.White
}
