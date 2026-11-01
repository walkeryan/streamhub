package com.streamhub.tv.feature.home

import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.model.Tile
import java.time.Instant
import org.junit.Assert.*
import org.junit.Test

class HomeCardCopyTest {
    private val now = Instant.parse("2026-11-01T01:00:00Z")
    private fun channel(start: String = "2026-11-01T00:30:00Z", stop: String = "2026-11-01T01:30:00Z") = Tile(
        "ch:test:1", MediaKind.CHANNEL, "Channel One", nowPlaying = Programme("p1", "ch:test:1", start, stop, "Game replay and highlights"),
    )
    @Test fun currentProgrammeRetainsProviderTitleAndChannel() {
        val copy = homeCardCopy(channel(), now)
        assertEquals("Game replay and highlights", copy.title)
        assertTrue(copy.detail.startsWith("Channel One · "))
        assertTrue(copy.onNow)
        assertEquals(0.5f, copy.progress!!, 0.001f)
    }
    @Test fun missingExpiredAndMalformedSchedulesNeverClaimOnNow() {
        assertFalse(homeCardCopy(Tile("ch:test:1", MediaKind.CHANNEL, "Channel One"), now).onNow)
        assertFalse(homeCardCopy(channel(stop = "2026-11-01T01:00:00Z"), now).onNow)
        assertFalse(homeCardCopy(channel(start = "invalid"), now).onNow)
        assertNull(homeCardCopy(channel(start = "invalid"), now).progress)
    }
    @Test fun movieProgressAndTitleRemainIntact() {
        val copy = homeCardCopy(Tile("lib:movie:1", MediaKind.MOVIE, "Movie One", year = 2024, progressPct = 42.0), now)
        assertEquals("Movie One", copy.title)
        assertEquals("2024", copy.detail)
        assertFalse(copy.onNow)
        assertEquals(0.42f, copy.progress!!, 0.001f)
    }
}
