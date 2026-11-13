package com.streamhub.tv.feature.search

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.SearchFeed
import com.streamhub.tv.core.data.model.SearchProgramme
import com.streamhub.tv.core.data.model.SearchResponse
import com.streamhub.tv.core.data.model.SourceBadge
import com.streamhub.tv.core.data.model.SourceType
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.data.model.searchParameters
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

class SearchRowsTest {
    private fun tile(inApp: Boolean? = null, state: String? = null, kind: MediaKind = MediaKind.MOVIE) = Tile(
        id = "tmdb:movie:603", kind = kind, title = "The Long Film", requestState = state,
        sources = inApp?.let { listOf(SourceBadge("s1", if (it) SourceType.PLEX else SourceType.STREAMING_SERVICE, "Source", playableInApp = it)) }.orEmpty(),
    )
    private val zone = ZoneId.of("America/Chicago")
    private val now = Instant.parse("2026-11-13T18:30:00Z") // Friday 12:30 local
    private val p = SearchProgramme("prog:1", "ch:s:5", "2026-11-13T18:00:00Z", "2026-11-13T19:00:00Z", "World News", channelNumber = 5, channelName = "News One",
        feeds = listOf(SearchFeed("prog:1", "ch:s:5", 5, "News One"), SearchFeed("prog:9", "ch:s:205", 205, "News One HD")))

    @Test fun aTitleThatPlaysHereCarriesATick() {
        assertEquals(TitleMark.PlaysHere, titleMark(tile(inApp = true)))
        assertEquals("✓", titleMarkText(titleMark(tile(inApp = true))))
        // Fulfilled and playing: the tick, not the request.
        assertEquals(TitleMark.PlaysHere, titleMark(tile(inApp = true, state = "available")))
    }

    @Test fun aTitleThatDoesNotPlayHereCarriesNothingUnlessARequestIsInProgress() {
        assertEquals(TitleMark.None, titleMark(tile()))
        assertNull(titleMarkText(titleMark(tile())))
        // A hand-off source is not something this app plays, and it is not a request either.
        assertEquals(TitleMark.None, titleMark(tile(inApp = false)))
        for (closed in listOf("declined", "failed", "available")) assertEquals(closed, TitleMark.None, titleMark(tile(state = closed)))
        for (open in listOf("pending_approval", "approved", "sent")) assertTrue(open, titleMark(tile(state = open)) is TitleMark.Requested)
        assertEquals("Requested", titleMarkText(titleMark(tile(state = "pending_approval"))))
        assertEquals(TitleMark.None, titleMark(tile(kind = MediaKind.CHANNEL)))
    }

    @Test fun aBroadcastIsOnNowByItsOwnTimes() {
        assertTrue(broadcastOnNow(p, now))
        assertFalse(broadcastOnNow(p, Instant.parse(p.stop)))
        assertFalse(broadcastOnNow(p.copy(onNow = true), Instant.parse(p.start).minusSeconds(1)))
        // Unreadable times: the server's word.
        assertTrue(broadcastOnNow(p.copy(start = "?", onNow = true), now))
        assertFalse(broadcastOnNow(p.copy(start = "?"), now))
    }

    @Test fun aStartIsSaidOnTheViewersClock() {
        assertEquals("Today 8:30 PM", broadcastStart("2026-11-14T02:30:00Z", now, zone))
        assertEquals("Tomorrow 9:00 AM", broadcastStart("2026-11-14T15:00:00Z", now, zone))
        assertEquals("Sun 7:00 PM", broadcastStart("2026-11-16T01:00:00Z", now, zone))
        assertEquals("", broadcastStart("soon", now, zone))
    }

    @Test fun aBroadcastNamesItsFirstChannelAndHowManyMoreCarryIt() {
        assertEquals("5 · News One +1 more", broadcastChannelLine(p))
        assertEquals("5 · News One", broadcastChannelLine(p.copy(feeds = p.feeds.take(1))))
        assertEquals("News One", broadcastChannelLine(p.copy(channelNumber = 0, feeds = emptyList())))
    }

    @Test fun aBroadcastIsRecordedAsTheProgrammeItIs() {
        val programme = p.copy(isSport = true, teams = listOf("A", "B"), recordingState = "scheduled").asProgramme()
        assertEquals("prog:1", programme.id)
        assertEquals("ch:s:5", programme.channelId)
        assertEquals(p.start, programme.start)
        assertEquals(listOf("A", "B"), programme.teams)
        assertEquals("scheduled", programme.recordingState)
    }

    @Test fun everyQueryIsAskedOnceOfTheHubAndOnceWithDiscoveryForTitles() {
        assertEquals(mapOf("q" to "the mill", "discover" to "false"), searchParameters("  the mill ", discover = false))
        assertEquals(mapOf("q" to "the mill", "kinds" to "movie,show"), searchParameters("the mill", discover = true))
        assertEquals("channels,programmes", searchParameters("x", false, listOf("programmes", "channels", "people", "channels"))["expand"])
        assertNull(searchParameters("x", false, emptyList())["expand"])
        // No language, source or kind filter is ever sent for the hub's own search.
        assertEquals(setOf("q", "discover"), searchParameters("x", false).keys)
    }

    @Test fun theAnswerDecodesWithItsBroadcastsSectionsAndDiscoveredTitles() {
        val r = StreamHubJson.decodeFromString(SearchResponse.serializer(), """
            {"results":[{"id":"lib:s:1","kind":"movie","title":"The Mill","sources":[]}],"metadata_available":true,"discovery_note":"","play_here":[],"open_in_app":[],
             "channels":[{"id":"ch:s:5","source_id":"s","number":5,"name":"News One","group":"News","now":null,"next":null}],
             "programmes":[{"id":"prog:1","channel_id":"ch:s:5","start":"2026-11-13T18:00:00Z","stop":"2026-11-13T19:00:00Z","title":"World News","on_now":true,"channel_number":5,"channel_name":"News One","channel_logo":null,
               "feeds":[{"programme_id":"prog:1","channel_id":"ch:s:5","channel_number":5,"channel_name":"News One"}]}],
             "discovered":["tmdb:movie:9"],"sections":{"titles":{"count":41,"more":true},"channels":{"count":1,"more":false},"programmes":{"count":1,"more":false}},
             "order":["channels","programmes","titles"],"attribution":"x"}
        """.trimIndent())
        assertEquals(listOf("lib:s:1"), r.titles.map { it.id })
        assertTrue(r.programmes.single().onNow)
        assertEquals("ch:s:5", r.programmes.single().feeds.single().channelId)
        assertTrue(r.sections.titles.more)
        assertEquals(41, r.sections.titles.count)
        assertEquals(listOf("tmdb:movie:9"), r.discovered)
        assertNull(r.channels.single().now)
        assertEquals(listOf("channels", "programmes", "titles"), r.order)
        // A server from before the unified search: titles in two lists, nothing else.
        val old = StreamHubJson.decodeFromString(SearchResponse.serializer(), """{"play_here":[{"id":"a","kind":"movie","title":"A","sources":[]}],"open_in_app":[{"id":"b","kind":"show","title":"B","sources":[]}],"channels":[]}""")
        assertEquals(listOf("a", "b"), old.titles.map { it.id })
        assertNull(old.discovered)
        assertTrue(old.programmes.isEmpty() && old.order.isEmpty() && !old.sections.titles.more)
    }
}
