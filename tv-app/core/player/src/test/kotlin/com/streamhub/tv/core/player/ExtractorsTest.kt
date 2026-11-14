package com.streamhub.tv.core.player

import androidx.media3.common.MimeTypes
import androidx.media3.extractor.ts.TsExtractor
import androidx.media3.test.utils.FakeTrackOutput
import androidx.media3.test.utils.TestUtil
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * delimited-avc.ts is a generated two-second test pattern (60 frames of H.264 at 30 fps with access
 * unit delimiters and B-frames, plus AAC), shaped like the live channels the player receives.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class ExtractorsTest {
    @Test
    fun `each video sample handed to the decoder is one whole frame`() {
        val bytes = javaClass.classLoader!!.getResourceAsStream("delimited-avc.ts")!!.use { it.readBytes() }
        val extractor = streamExtractors().createExtractors().single { it is TsExtractor }
        val output = TestUtil.extractAllSamplesFromByteArray(extractor, bytes)
        val video = (0 until output.numberOfTracks)
            .map { output.trackOutputs.valueAt(it) as FakeTrackOutput }
            .single { it.lastFormat?.sampleMimeType == MimeTypes.VIDEO_H264 }
        // With FLAG_DETECT_ACCESS_UNITS this was 126: delimiters and SEI split off as samples of their own.
        assertEquals(60, video.sampleCount)
    }
}
