package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

class ArchiveWindowTest {
    private val window=RewindCapability("provider_archive", "2026-11-01T18:00:00Z", "2026-11-01T19:00:00Z")
    @Test fun boundedMinuteWindowIncludesStartButNotLiveEdge() {
        val choices=archiveChoices(window)
        assertEquals(60,choices.size)
        assertEquals(Instant.parse("2026-11-01T18:59:00Z"),choices.first())
        assertEquals(Instant.parse(window.advertisedStart),choices.last())
        assertFalse(archiveSelectionValid(window,Instant.parse(window.advertisedEnd)))
    }
    @Test fun unavailableMalformedAndUnsupportedGranularityDoNotPromiseRewind() {
        assertTrue(archiveChoices(RewindCapability()).isEmpty())
        assertTrue(archiveChoices(window.copy(advertisedEnd="bad")).isEmpty())
        assertTrue(archiveChoices(window.copy(granularitySeconds=1)).isEmpty())
        assertTrue(archiveChoices(window.copy(advertisedStart=window.advertisedEnd)).isEmpty())
    }
    @Test fun expiredAndNonMinuteSelectionAreRejected() {
        val at=Instant.parse(window.advertisedStart)
        assertTrue(archiveSelectionValid(window,at))
        assertFalse(archiveSelectionValid(window.copy(advertisedStart="2026-11-01T18:01:00Z",advertisedEnd="2026-11-01T19:01:00Z"),at))
        assertFalse(archiveSelectionValid(window,at.plusSeconds(1)))
    }
    @Test fun finiteArchiveIsNotLiveEvenWithChannelIdAndGoLiveClearsSelection() {
        val descriptor=Json { ignoreUnknownKeys=true }.decodeFromString<PlaybackDescriptor>("""{"id":"ch:s:7","mode":"proxy","live":false,"archive_start":"2026-11-01T18:00:00Z","archive_end":"2026-11-01T19:00:00Z"}""")
        assertFalse(descriptor.live)
        assertNotNull(descriptor.archiveStart)
        assertNull(PlaybackRequest(descriptor.id).rewindAt)
        assertNull(PlaybackRequest(descriptor.id, rewindAt=window.advertisedStart).catchupProgrammeId)
    }
}
