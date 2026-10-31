package com.streamhub.tv.core.player

import android.net.Uri
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode

/**
 * PlaybackDescriptor → MediaItem.
 * MIME is taken from the descriptor (server knows best); when absent it's inferred from mode/URL.
 * Explicit MIME matters: IPTV "progressive TS" URLs usually have no extension, and without it
 * DefaultMediaSourceFactory would fall back to progressive extraction for an HLS playlist.
 */
object MediaItemMapper {

    fun mimeFor(d: PlaybackDescriptor): String? {
        d.mime?.lowercase()?.let { m ->
            return when (m) {
                "application/x-mpegurl", "application/vnd.apple.mpegurl", "audio/mpegurl" -> MimeTypes.APPLICATION_M3U8
                "application/dash+xml" -> MimeTypes.APPLICATION_MPD
                "video/mp2t" -> MimeTypes.VIDEO_MP2T
                else -> m
            }
        }
        if (d.mode == PlaybackMode.HLS) return MimeTypes.APPLICATION_M3U8
        val path = d.url?.substringBefore('?')?.lowercase().orEmpty()
        return when {
            path.endsWith(".m3u8") -> MimeTypes.APPLICATION_M3U8
            path.endsWith(".mpd") -> MimeTypes.APPLICATION_MPD
            path.endsWith(".ts") -> MimeTypes.VIDEO_MP2T
            path.endsWith(".mp4") || path.endsWith(".m4v") -> MimeTypes.VIDEO_MP4
            path.endsWith(".mkv") -> MimeTypes.VIDEO_MATROSKA
            d.live -> MimeTypes.VIDEO_MP2T // Xtream/M3U live default: progressive MPEG-TS
            else -> null
        }
    }

    fun toMediaItem(d: PlaybackDescriptor): MediaItem {
        val url = requireNotNull(d.url) { "Descriptor ${d.id} has no url (mode=${d.mode})" }
        val builder = MediaItem.Builder()
            .setMediaId(d.id)
            .setUri(Uri.parse(url))
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(d.title)
                    .setSubtitle(d.subtitle)
                    .setArtworkUri(d.poster?.let(Uri::parse))
                    .build(),
            )
        mimeFor(d)?.let(builder::setMimeType)
        if (d.live) {
            builder.setLiveConfiguration(
                MediaItem.LiveConfiguration.Builder()
                    .setTargetOffsetMs(LIVE_TARGET_OFFSET_MS)
                    .setMinPlaybackSpeed(0.97f)
                    .setMaxPlaybackSpeed(1.03f)
                    .build(),
            )
        }
        return builder.build()
    }

    const val LIVE_TARGET_OFFSET_MS = 8_000L
}
