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
        if (pinsPosition(d)) {
            // A room's shared stream is a playlist that grows faster than it plays. The player would
            // take it for a live stream it has fallen behind on and speed up to catch the end of it;
            // the room clock, not the playlist's edge, says where a member belongs.
            builder.setLiveConfiguration(MediaItem.LiveConfiguration.Builder().setMinPlaybackSpeed(1f).setMaxPlaybackSpeed(1f).build())
        } else if (d.live) {
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

    /**
     * A watch room's one shared stream (`stream_epoch` set): it is always started at an exact
     * position, the first frame included, and played at 1×. Its media time zero is [timeZeroMs] into
     * the title.
     */
    fun pinsPosition(d: PlaybackDescriptor): Boolean = d.streamEpoch != null && !d.live

    /** Where in the title the stream's media time zero is: `start_position_ms` for a room's shared stream, 0 otherwise. */
    fun timeZeroMs(d: PlaybackDescriptor): Long = if (pinsPosition(d)) (d.startPositionMs ?: 0).coerceAtLeast(0) else 0

    const val LIVE_TARGET_OFFSET_MS = 8_000L
}
