package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.model.BufferWindow
import java.time.Duration
import java.time.Instant

/** Positions are absolute so a refreshing live window cannot silently move a viewer's selection. */
internal data class WatchedTimeline(val start: Instant, val end: Instant) {
    val durationMs: Long get() = Duration.between(start, end).toMillis()
    fun clamp(at: Instant) = at.coerceIn(start, end)
    fun move(at: Instant, deltaMs: Long): Instant = clamp(clamp(at).plusMillis(deltaMs))
    fun progress(at: Instant): Float = (Duration.between(start, clamp(at)).toMillis().toDouble() / durationMs).toFloat()
    fun seekAt(at: Instant): Instant? = clamp(at).takeIf { it < end }

    companion object {
        fun from(window: BufferWindow?): WatchedTimeline? {
            val start = window?.start?.let { runCatching { Instant.parse(it) }.getOrNull() } ?: return null
            val end = window.end?.let { runCatching { Instant.parse(it) }.getOrNull() } ?: return null
            return if (Duration.between(start, end).toMillis() > 0) WatchedTimeline(start, end) else null
        }
    }
}

/** The upstream playlist can already contain segments when this viewer tunes in. */
internal fun localTuneWindow(startMs: Long, durationMs: Long, tunedForMs: Long): WatchedTimeline? {
    val retained = minOf(durationMs, tunedForMs)
    if (retained <= 0) return null
    val end = Instant.ofEpochMilli(startMs).plusMillis(durationMs)
    return WatchedTimeline(end.minusMillis(retained), end)
}

internal fun optionalBufferUnavailable(error: Exception): Boolean =
    error is com.streamhub.tv.core.data.api.ApiException && error.status == 409 &&
        error.error?.code in setOf("buffer_unsupported", "buffer_session_active", "buffer_storage_unverified",
            "buffer_storage_full", "buffer_busy", "recording_disabled")
