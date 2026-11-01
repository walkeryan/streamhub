package com.streamhub.tv.feature.home

import com.streamhub.tv.core.data.model.SportsEvent
import com.streamhub.tv.core.data.model.SportsEventsPage
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

class SportsEventTest {
    private val now = Instant.parse("2026-11-01T18:30:00Z")
    private val event = SportsEvent("event-one", "Actual EPG title", "American football", "2026-11-01T18:00:00Z", "2026-11-01T21:00:00Z", "current")
    @Test fun currentScheduleIsNotAutomaticallyLive() {
        assertEquals("On now", sportsEventBadge(event))
        assertTrue(sportsCanWatch(event, now))
        assertEquals("LIVE · guide tagged", sportsEventBadge(event.copy(liveEvidence=true)))
    }
    @Test fun futureExpiredAndMalformedSchedulesNeverWatch() {
        assertFalse(sportsCanWatch(event.copy(status="upcoming"), now))
        assertFalse(sportsCanWatch(event, Instant.parse(event.stop)))
        assertFalse(sportsCanWatch(event, Instant.parse(event.start).minusSeconds(1)))
        assertFalse(sportsCanWatch(event.copy(start="invalid"), now))
        assertFalse(sportsCanWatch(event.copy(status="ended"), now))
    }
    @Test fun replayIsCurrentBroadcastNotArchiveOrLive() {
        val replay = event.copy(replay=true, liveEvidence=true)
        assertEquals("Replay listing", sportsEventBadge(replay))
        assertTrue(sportsCanWatch(replay, now))
        assertFalse(sportsCanWatch(replay.copy(status="ended"), now))
    }
    @Test fun exactFeedAndUnknownLanguageSurviveContract() {
        val page = Json { ignoreUnknownKeys=true }.decodeFromString<SportsEventsPage>("""{"events":[{"id":"e","title":"EPG event","sport":"Soccer","start":"2026-11-01T18:00:00Z","stop":"2026-11-01T21:00:00Z","status":"current","grouping_evidence":"same_source_programme","feeds":[{"programme_id":"prog:4","channel_id":"ch:source:7","channel_name":"Feed 7","source_id":"source","source_name":"IPTV","language":null,"language_verified":false}]}],"truncated":true}""")
        assertTrue(page.truncated)
        assertEquals("ch:source:7", page.events.single().feeds.single().channelId)
        assertNull(page.events.single().feeds.single().language)
        assertFalse(page.events.single().liveEvidence)
    }
}
