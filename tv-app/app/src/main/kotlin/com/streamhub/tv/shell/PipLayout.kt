package com.streamhub.tv.shell

import com.streamhub.tv.core.data.api.StreamHubJson
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.roundToInt

/*
 * Picture-in-picture geometry, as pure functions so every rule is unit-tested (PipLayoutTest).
 *
 * What Android guarantees, and what it only tries:
 *  - Guaranteed: the PiP window keeps the aspect ratio it is given (clamped here to the platform's
 *    1:2.39 … 2.39:1, outside which enterPictureInPictureMode throws), and a source rect hint is only
 *    used for the enter/leave animation. Neither moves or shrinks the window: on Android TV its size is
 *    the system's choice.
 *  - Best effort: keep-clear areas (`View.setPreferKeepClearRects`, Android 13 / API 33+). The system's
 *    PiP placement tries to keep the window off the areas the focused window declares, but it may not
 *    move it at all, or only partly, when every placement would cover something (or the areas are too
 *    big), and an OEM build may ignore them. Below API 33 there is no such call.
 *  - So the web page also degrades on its own (web-app/src/player/pipAvoid.ts): the TV tells it where
 *    the PiP window is when it can read that ([PxRect] from the player's window bounds, best effort too),
 *    and whether keep-clear is available at all; when the window still covers what the page asked to keep
 *    clear, or nothing is known, the page keeps room at that corner so focused rows scroll out from under it.
 */

/** A rectangle in device pixels (a View's or the screen's coordinates); right/bottom exclusive, like android.graphics.Rect. */
data class PxRect(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    val width: Int get() = right - left
    val height: Int get() = bottom - top
    val isEmpty: Boolean get() = right <= left || bottom <= top
}

/** A rectangle in the page's CSS pixels, relative to its viewport (getBoundingClientRect). */
data class CssRect(val x: Double, val y: Double, val w: Double, val h: Double)

/** The page's `keep_clear` message: what it wants kept clear, and how wide its viewport is in CSS px. */
data class KeepClearReport(val rects: List<CssRect>, val viewportWidth: Double?)

private const val MAX_KEEP_CLEAR_JSON = 4 * 1024
private const val MAX_KEEP_CLEAR_RECTS = 8
/** Far more than any viewport: a coordinate beyond this is not a layout, it is noise. */
private const val MAX_CSS = 100_000.0

/** Android's PiP aspect ratio limits (config_pictureInPictureMin/MaxAspectRatio): 1:2.39 to 2.39:1. */
const val PIP_MAX_ASPECT = 2.39
const val PIP_MIN_ASPECT = 1 / 2.39

/**
 * Reads a page → TV `message(json)` of kind `keep_clear`:
 * `{"v":1,"kind":"keep_clear","viewport_width":1920,"rects":[{"x":…,"y":…,"w":…,"h":…}, …]}` (CSS px).
 * At most [MAX_KEEP_CLEAR_RECTS] rects; an empty list clears. Null for anything malformed.
 */
fun parseKeepClear(json: String): KeepClearReport? {
    if (json.length > MAX_KEEP_CLEAR_JSON) return null
    val root = runCatching { StreamHubJson.parseToJsonElement(json) }.getOrNull() as? JsonObject ?: return null
    val v = (root["v"] as? JsonPrimitive)?.takeIf { !it.isString }?.longOrNull
    if (v != BRIDGE_VERSION.toLong() || (root["kind"] as? JsonPrimitive)?.takeIf { it.isString }?.content != "keep_clear") return null
    val list = root["rects"] as? JsonArray ?: return null
    if (list.size > MAX_KEEP_CLEAR_RECTS) return null
    fun num(o: JsonObject, key: String): Double? =
        (o[key] as? JsonPrimitive)?.takeIf { !it.isString }?.doubleOrNull?.takeIf { it.isFinite() && it in -MAX_CSS..MAX_CSS }
    val rects = list.map { item ->
        val o = item as? JsonObject ?: return null
        val x = num(o, "x") ?: return null
        val y = num(o, "y") ?: return null
        val w = num(o, "w")?.takeIf { it >= 0 } ?: return null
        val h = num(o, "h")?.takeIf { it >= 0 } ?: return null
        CssRect(x, y, w, h)
    }
    val width = root["viewport_width"]?.let { el -> (el as? JsonPrimitive)?.takeIf { !it.isString }?.doubleOrNull?.takeIf { it.isFinite() && it > 0 && it <= MAX_CSS } }
    return KeepClearReport(rects, width)
}

/**
 * Device pixels per CSS pixel in the WebView: its width over the page's viewport width when the page
 * said it (the shell lays out 1920 CSS px across however many device px the screen has), else the
 * WebView's own scale ([fallbackScale], `WebView.getScale()`), else 1.
 */
fun cssToPxScale(viewWidthPx: Int, viewportCssWidth: Double?, fallbackScale: Float): Double = when {
    viewWidthPx > 0 && viewportCssWidth != null && viewportCssWidth > 0 -> viewWidthPx / viewportCssWidth
    fallbackScale.isFinite() && fallbackScale > 0f -> fallbackScale.toDouble()
    else -> 1.0
}

/**
 * The page's keep-clear rects in the WebView's own pixels, for `setPreferKeepClearRects`: scaled,
 * rounded outwards (a rect never shrinks off what it should cover), clipped to the view, empty ones dropped.
 */
fun keepClearPx(report: KeepClearReport, viewWidthPx: Int, viewHeightPx: Int, fallbackScale: Float): List<PxRect> {
    if (viewWidthPx <= 0 || viewHeightPx <= 0) return emptyList()
    val scale = cssToPxScale(viewWidthPx, report.viewportWidth, fallbackScale)
    return report.rects.mapNotNull { r ->
        PxRect(
            floor(r.x * scale).toInt().coerceIn(0, viewWidthPx),
            floor(r.y * scale).toInt().coerceIn(0, viewHeightPx),
            ceil((r.x + r.w) * scale).toInt().coerceIn(0, viewWidthPx),
            ceil((r.y + r.h) * scale).toInt().coerceIn(0, viewHeightPx),
        ).takeIf { !it.isEmpty }
    }
}

/**
 * The PiP window ([pip], screen px) in the page's CSS px, for the page to keep its corner clear.
 * [webViewLeft]/[webViewTop] are the WebView's place on screen. Null when it does not overlap the view.
 */
fun pipInCss(pip: PxRect, webViewLeft: Int, webViewTop: Int, viewWidthPx: Int, viewHeightPx: Int, viewportCssWidth: Double?, fallbackScale: Float): CssRect? {
    if (pip.isEmpty || viewWidthPx <= 0 || viewHeightPx <= 0) return null
    val left = (pip.left - webViewLeft).coerceIn(0, viewWidthPx)
    val top = (pip.top - webViewTop).coerceIn(0, viewHeightPx)
    val right = (pip.right - webViewLeft).coerceIn(0, viewWidthPx)
    val bottom = (pip.bottom - webViewTop).coerceIn(0, viewHeightPx)
    if (right <= left || bottom <= top) return null
    val scale = cssToPxScale(viewWidthPx, viewportCssWidth, fallbackScale)
    fun css(px: Int) = (px / scale * 10).roundToInt() / 10.0
    return CssRect(css(left), css(top), css(right - left), css(bottom - top))
}

/**
 * The picture's displayed aspect ratio as a fraction for `PictureInPictureParams.setAspectRatio`,
 * clamped to what Android accepts. [pixelRatio] is the video's pixel aspect (anamorphic video).
 * Null when the size is not known yet (the caller keeps 16:9).
 */
fun pipAspect(videoWidth: Int, videoHeight: Int, pixelRatio: Float = 1f): Pair<Int, Int>? {
    if (videoWidth <= 0 || videoHeight <= 0) return null
    val par = if (pixelRatio.isFinite() && pixelRatio > 0f) pixelRatio.toDouble() else 1.0
    val ratio = (videoWidth * par / videoHeight).coerceIn(PIP_MIN_ASPECT, PIP_MAX_ASPECT)
    // Thousandths are plenty for a window shape, and keep both numbers small; never outside the limits after rounding.
    val num = (ratio * 1000).roundToInt()
    val clamped = when {
        num / 1000.0 > PIP_MAX_ASPECT -> floor(PIP_MAX_ASPECT * 1000).toInt()
        num / 1000.0 < PIP_MIN_ASPECT -> ceil(PIP_MIN_ASPECT * 1000).toInt()
        else -> num
    }
    return clamped to 1000
}

/**
 * Where the picture sits on screen at [aspect] (width to height) in a [viewWidth]×[viewHeight] window:
 * the centred, fitted rect, which is what `setSourceRectHint` wants (the part of the activity the PiP
 * window will show, so the animation starts from the picture rather than the letterboxed screen).
 */
fun pipSourceRect(viewWidth: Int, viewHeight: Int, aspect: Pair<Int, Int>?): PxRect? {
    if (viewWidth <= 0 || viewHeight <= 0 || aspect == null || aspect.first <= 0 || aspect.second <= 0) return null
    val ratio = aspect.first.toDouble() / aspect.second
    return if (viewWidth.toDouble() / viewHeight > ratio) {
        val w = (viewHeight * ratio).roundToInt().coerceIn(1, viewWidth)
        val left = (viewWidth - w) / 2
        PxRect(left, 0, left + w, viewHeight)
    } else {
        val h = (viewWidth / ratio).roundToInt().coerceIn(1, viewHeight)
        val top = (viewHeight - h) / 2
        PxRect(0, top, viewWidth, top + h)
    }
}
