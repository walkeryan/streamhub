package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.MediaInfo
import com.streamhub.tv.core.data.model.MediaInfoAudio
import com.streamhub.tv.core.data.model.MediaInfoVideo
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.player.PlayerStats
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Locale

class StreamInfoTest {
    private val stats = PlayerStats(width = 1920, height = 1080, frameRate = 23.976f, videoCodec = "hevc", audioCodec = "mp4a-latm", droppedFrames = 3, bufferedMs = 42_400, bitrateEstimateBps = 18_600_000)
    private fun lines(info: MediaInfo?, s: PlayerStats = stats) = streamInfoLines(info, s, Locale.ENGLISH).associate { it.label to it.value }

    @Test fun theServersFactsComeFirstThenThePlayersOwnNumbers() {
        val info = MediaInfo(
            sourceName = "Living room library", container = "matroska", bitrateKbps = 12_400,
            video = MediaInfoVideo("hevc", 3840, 2160, 23.976, "hdr10"), audio = MediaInfoAudio("eac3", 6, "eng"),
            delivery = "transcode", encoder = "hardware", outputWidth = 1920, outputHeight = 1080,
        )
        val shown = streamInfoLines(info, stats, Locale.ENGLISH)
        assertEquals(listOf("Source", "File", "Video", "Audio", "Delivery", "Playing", "Dropped frames", "Buffered", "Bandwidth estimate"), shown.map { it.label })
        val by = shown.associate { it.label to it.value }
        assertEquals("Living room library", by["Source"])
        assertEquals("matroska · 12.4 Mbit/s", by["File"])
        assertEquals("HEVC · 3840×2160 · 24 fps · HDR10", by["Video"])
        assertEquals("EAC3 · 5.1 · English", by["Audio"])
        assertEquals("Transcode (hardware encoder), delivered at 1920×1080", by["Delivery"])
        assertEquals("1920×1080 · 24 fps · HEVC + MP4A-LATM", by["Playing"])
        assertEquals("3", by["Dropped frames"])
        assertEquals("42.4 s", by["Buffered"])
        assertEquals("18.6 Mbit/s", by["Bandwidth estimate"])
    }

    @Test fun whatNobodyKnowsIsLeftOutAndTheDeliveryIsAlwaysSaid() {
        val by = lines(MediaInfo(delivery = "direct"))
        assertEquals(setOf("Delivery", "Playing", "Dropped frames", "Buffered", "Bandwidth estimate"), by.keys)
        assertEquals("Direct (as the source sends it)", by["Delivery"])
        assertEquals("Remux (repackaged, video untouched)", lines(MediaInfo(delivery = "remux"))["Delivery"])
        assertEquals("Transcode", lines(MediaInfo(delivery = "transcode"))["Delivery"])
        assertEquals("Passthrough", lines(MediaInfo(delivery = "passthrough"))["Delivery"])
        // A language code is never shown raw, and an unknown one is left out.
        assertEquals("AAC · Stereo", lines(MediaInfo(audio = MediaInfoAudio("aac", 2, "und")))["Audio"])
        assertEquals("AAC · Stereo · French", lines(MediaInfo(audio = MediaInfoAudio("aac", 2, "fre")))["Audio"])
        assertEquals("640 kbit/s", lines(MediaInfo(bitrateKbps = 640))["File"])
        assertNull(lines(MediaInfo(video = MediaInfoVideo()))["Video"])
    }

    @Test fun withoutMediaInfoThePlayersNumbersStillShow() {
        val by = lines(null, PlayerStats())
        assertEquals(setOf("Playing", "Dropped frames", "Buffered", "Bandwidth estimate"), by.keys)
        assertEquals("No picture yet", by["Playing"])
        assertEquals("0", by["Dropped frames"])
        assertEquals("0 s", by["Buffered"])
        assertEquals("Not measured yet", by["Bandwidth estimate"])
    }

    @Test fun mediaInfoDecodesFromTheDescriptorAndIsAbsentOnOlderServers() {
        val d = StreamHubJson.decodeFromString(PlaybackDescriptor.serializer(), """
            {"id":"lib:s:1","mode":"hls","url":"/stream/t/index.m3u8","live":false,"title":"x",
             "media_info":{"source_name":"Library","container":"mov","bitrate_kbps":8000,"video":{"codec":"h264","width":1920,"height":800,"frame_rate":24,"dynamic_range":"sdr"},
               "audio":{"codec":"ac3","channels":6,"language":"eng"},"delivery":"remux","output_width":1920,"output_height":800}}
        """.trimIndent())
        assertEquals("remux", d.mediaInfo?.delivery)
        assertEquals(800, d.mediaInfo?.video?.height)
        assertEquals("ac3", d.mediaInfo?.audio?.codec)
        assertNull(d.mediaInfo?.encoder)
        val old = StreamHubJson.decodeFromString(PlaybackDescriptor.serializer(), """{"id":"lib:s:1","mode":"direct","url":"/stream/t","title":"x"}""")
        assertNull(old.mediaInfo)
        assertTrue(streamInfoLines(old.mediaInfo, stats, Locale.ENGLISH).none { it.label == "Delivery" })
        assertFalse(lines(d.mediaInfo).values.any { it.contains("http") })
    }
}
