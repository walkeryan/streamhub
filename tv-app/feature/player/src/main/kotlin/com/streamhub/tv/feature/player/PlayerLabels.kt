package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackThumbnails
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/*
 * The small pure pieces of the revamped player: what its title block says, the profile preferences it
 * reads, and the address of a scrub thumbnail. Unit-tested.
 */

/** The profile preference (in `/profiles/{id}/prefs`) that lets the next episode start by itself. */
const val AUTOPLAY_NEXT_PREF = "autoplay_next"

/** `autoplay_next` as stored: anything but an explicit `false` (missing, null, garbage) means on. */
fun autoplayNextPref(value: JsonElement?): Boolean = (value as? JsonPrimitive)?.takeIf { !it.isString }?.booleanOrNull ?: true

/** The profile preference that makes Back shrink a playing title into the mini player instead of closing it. */
const val BACK_MINIMISES_PREF = "back_minimises"

/** `back_minimises` as stored: only an explicit `true` turns it on (missing = Back closes the player). */
fun backMinimisesPref(value: JsonElement?): Boolean = (value as? JsonPrimitive)?.takeIf { !it.isString }?.booleanOrNull ?: false

/** "S2 · E1" for an episode that knows its numbers; null otherwise. */
fun episodeCode(d: PlaybackDescriptor): String? {
    val s = d.seasonNumber ?: return null
    val e = d.episodeNumber ?: return null
    return "S$s · E$e"
}

/**
 * The player's title block: a small line above the big title. An episode reads
 * "Show · Season 2 · Episode 1" over the episode's name; anything else keeps its own title and subtitle.
 */
fun titleBlock(d: PlaybackDescriptor): Pair<String?, String> {
    val show = d.show?.title?.takeIf { it.isNotBlank() }
    if (show != null) {
        val kicker = listOfNotNull(show, d.seasonNumber?.let { "Season $it" }, d.episodeNumber?.let { "Episode $it" }).joinToString(" · ")
        return kicker to d.title.ifBlank { show }
    }
    return d.subtitle?.takeIf { it.isNotBlank() } to d.title
}

/**
 * The frame nearest [positionMs]: the position rounded down to the thumbnail interval (so every
 * press within one interval asks for one image, which the image cache then keeps), appended as
 * `ms=` to the grant URL. A relative URL is taken against [serverUrl]. Null when there is nothing usable.
 */
fun thumbnailUrl(t: PlaybackThumbnails?, positionMs: Long, serverUrl: String?): String? {
    if (t == null || t.url.isBlank() || t.intervalMs <= 0) return null
    val base = when {
        t.url.startsWith("http://") || t.url.startsWith("https://") -> t.url
        t.url.startsWith("/") && serverUrl != null -> serverUrl.trimEnd('/') + t.url
        else -> return null
    }
    val at = (positionMs.coerceAtLeast(0) / t.intervalMs) * t.intervalMs
    return base + (if ('?' in base) "&" else "?") + "ms=" + at
}

/** A track's label as the control row shows it: "English · 5.1 · EAC3" reads "English 5.1". */
fun shortTrackLabel(label: String): String = label.split(" · ").take(2).joinToString(" ").ifBlank { label }

/** `1:02:03` / `4:05`. */
fun clock(ms: Long): String {
    val s = ms.coerceAtLeast(0) / 1000
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, (s / 60) % 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
}
