package com.streamhub.tv.core.player

import androidx.media3.common.MimeTypes
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import org.junit.Assert.assertEquals
import org.junit.Test

class MediaItemMapperTest {
    private fun d(url: String, mime: String? = null, mode: PlaybackMode = PlaybackMode.DIRECT, live: Boolean = false) =
        PlaybackDescriptor(id = "x", mode = mode, url = url, mime = mime, live = live)

    @Test fun explicitMimeWins() = assertEquals(MimeTypes.APPLICATION_M3U8, MediaItemMapper.mimeFor(d("http://a/b", "application/x-mpegURL")))
    @Test fun hlsMode() = assertEquals(MimeTypes.APPLICATION_M3U8, MediaItemMapper.mimeFor(d("http://a/stream/tok", mode = PlaybackMode.HLS)))
    @Test fun dashByExt() = assertEquals(MimeTypes.APPLICATION_MPD, MediaItemMapper.mimeFor(d("http://a/m.mpd?x=1")))
    @Test fun liveXtreamWithoutExtIsTs() = assertEquals(MimeTypes.VIDEO_MP2T, MediaItemMapper.mimeFor(d("http://p:8080/u/p/123", live = true)))
    @Test fun vodUnknownIsNull() = assertEquals(null, MediaItemMapper.mimeFor(d("http://plex/library/parts/1/file")))
}
