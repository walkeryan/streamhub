package com.streamhub.tv.feature.home

import com.streamhub.tv.core.data.model.NativeSportsFilters
import com.streamhub.tv.core.data.model.PlaybackRequest
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.model.SportsChannel
import com.streamhub.tv.core.data.model.SportsChannelsPage
import com.streamhub.tv.core.data.model.SportsEvent
import com.streamhub.tv.core.data.model.SportsEventsPage
import com.streamhub.tv.core.data.model.SportsFeed
import com.streamhub.tv.core.data.model.SportsKinds
import com.streamhub.tv.core.data.model.sportsEventParameters
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale

class SportsEventTest {
    private val zone = ZoneId.of("America/Chicago")
    private val now = Instant.parse("2026-11-01T18:30:00Z") // 12:30 local
    private val today = LocalDate.of(2026, 11, 1)
    private fun feed(n: Int, quality: String? = null) = SportsFeed("prog:$n", "ch:s:$n", "Network $n", "s", "IPTV", channelNumber = n, quality = quality)
    private val event = SportsEvent("event-one", "Actual EPG title", "American football", "2026-11-01T18:00:00Z", "2026-11-01T21:00:00Z", "current", feeds = listOf(feed(7)))
    private fun at(id: String, start: String, stop: String, status: String = "upcoming") = event.copy(id = id, start = start, stop = stop, status = status)
    private val gameFeed = SportsEvent("slot", "Rovers at United", "Soccer", "2026-11-01T06:00:00Z", "2026-11-03T06:00:00Z", SportsKinds.UNSCHEDULED, kind = SportsKinds.CHANNEL, league = "LEAGUE PASS", feeds = listOf(feed(301)))

    @Test fun aCurrentListingIsLiveOnlyWhereTheGuideSaysSo() {
        assertEquals("On now", sportsLiveBadge(event))
        assertEquals("LIVE", sportsLiveBadge(event.copy(liveEvidence = true)))
        assertEquals("Replay", sportsLiveBadge(event.copy(replay = true, liveEvidence = true)))
    }

    @Test fun liveIsByTheClockAndNeverForUnreadableTimesOrGameFeeds() {
        assertTrue(sportsIsLive(event, now))
        assertFalse(sportsIsLive(event, Instant.parse(event.stop)))
        assertFalse(sportsIsLive(event, Instant.parse(event.start).minusSeconds(1)))
        assertFalse(sportsIsLive(event.copy(start = "invalid"), now))
        assertFalse(sportsIsLive(gameFeed, now))
        assertFalse(sportsIsAhead(gameFeed, Instant.parse("2026-10-31T00:00:00Z")))
        assertTrue(sportsIsAhead(event, Instant.parse(event.start).minusSeconds(1)))
    }

    @Test fun sectionsAreLiveThenDaysOnTheLocalClockThenGameFeeds() {
        val tonight = at("tonight", "2026-11-02T01:00:00Z", "2026-11-02T04:00:00Z")      // 19:00 local, still today
        val lateTonight = at("late", "2026-11-02T05:30:00Z", "2026-11-02T08:00:00Z")      // 23:30 local, today
        val tomorrow = at("tomorrow", "2026-11-02T18:00:00Z", "2026-11-02T21:00:00Z")     // 12:00 local tomorrow
        val over = at("over", "2026-11-01T12:00:00Z", "2026-11-01T15:00:00Z", status = "ended")
        val sections = sportsSections(listOf(tomorrow, gameFeed, lateTonight, event, tonight, over), now, zone, today, today)
        assertEquals(listOf("event-one"), sections.live.map { it.id })
        assertEquals(listOf(today, today.plusDays(1)), sections.days.map { it.date })
        assertEquals(listOf("tonight", "late"), sections.days[0].events.map { it.id })
        assertEquals(listOf("tomorrow"), sections.days[1].events.map { it.id })
        assertEquals(listOf("slot"), sections.gameFeeds.map { it.id })
        assertFalse(sections.isEmpty)
    }

    @Test fun anotherDayListsNeitherLiveGamesNorGameFeedsNorTheEveningBefore() {
        val day = today.plusDays(2)
        val lateBefore = at("late-before", "2026-11-03T05:30:00Z", "2026-11-03T08:00:00Z") // 23:30 local the day before
        val onTheDay = at("on-the-day", "2026-11-03T20:00:00Z", "2026-11-03T23:00:00Z")
        val sections = sportsSections(listOf(lateBefore, onTheDay, event, gameFeed), now, zone, day, today)
        assertTrue(sections.live.isEmpty())
        assertTrue(sections.gameFeeds.isEmpty())
        assertEquals(listOf("on-the-day"), sections.days.single().events.map { it.id })
        assertTrue(sportsSections(emptyList(), now, zone, today, today).isEmpty)
    }

    @Test fun aGameFeedIsShownButClaimsNothing() {
        // The old screen listed only current, upcoming and ended: a dateless game feed was dropped.
        val sections = sportsSections(listOf(gameFeed), now, zone, today, today)
        assertEquals(listOf(gameFeed), sections.gameFeeds)
        assertTrue(sections.live.isEmpty())
        assertTrue(sections.days.isEmpty())
        assertEquals(SportsAction.Play("ch:s:301"), sportsAction(gameFeed, now))
        assertEquals(SportsAction.ChooseFeed(gameFeed.copy(feeds = listOf(feed(301), feed(302)))), sportsAction(gameFeed.copy(feeds = listOf(feed(301), feed(302))), now))
    }

    @Test fun theCardNamesTheSidesWhenTheServerFoundThem() {
        assertEquals("Actual EPG title", sportsEventTitle(event))
        assertEquals("Rovers at United", sportsEventTitle(event.copy(title = "LEAGUE: Rovers @ United (Week 3)", away = "Rovers", home = "United")))
        assertEquals("Actual EPG title", sportsEventTitle(event.copy(home = "United", away = null)))
    }

    @Test fun leagueChannelQualityAndTimeLines() {
        assertEquals("Network 7 · Ch 7", sportsChannelLine(event.feeds))
        assertEquals("2 channels", sportsChannelLine(listOf(feed(7), feed(8))))
        assertEquals("LEAGUE PASS · Network 301 · Ch 301", sportsLeagueLine(gameFeed))
        assertEquals("Network 7 · Ch 7", sportsLeagueLine(event))
        assertNull(sportsQuality(event))
        assertEquals("FHD", sportsQuality(event.copy(feeds = listOf(feed(7), feed(8, "FHD")))))
        assertEquals("12:00 PM", sportsTime(event.start, zone))
        assertEquals("Started 12:00 PM", sportsStarted(event, zone))
        assertEquals("On now", sportsStarted(event.copy(start = "invalid"), zone))
    }

    @Test fun daysAreNamedAndNeverInThePast() {
        assertEquals("Today", sportsDayLabel(today, today))
        assertEquals("Tomorrow", sportsDayLabel(today.plusDays(1), today))
        assertEquals("Tuesday, Nov 3", sportsDayLabel(today.plusDays(2), today))
        assertEquals(7, sportsDayChoices(today).size)
        assertEquals(today, sportsDayChoices(today).first())
        assertEquals(today, sportsDate(null, today))
        assertEquals(today, sportsDate("2026-10-30", today))
        assertEquals(today, sportsDate("not a date", today))
        assertEquals(today.plusDays(3), sportsDate("2026-11-04", today))
        assertEquals(today, sportsDate("2026-12-25", today))
        assertEquals(2, sportsDaysAsked(today, today))
        assertEquals(1, sportsDaysAsked(today.plusDays(1), today))
    }

    @Test fun selectPlaysOneFeedAsksForSeveralAndOpensAnUpcomingGame() {
        assertEquals(SportsAction.Play("ch:s:7"), sportsAction(event, now))
        val several = event.copy(feeds = listOf(feed(7), feed(8)))
        assertEquals(SportsAction.ChooseFeed(several), sportsAction(several, now))
        val ahead = at("ahead", "2026-11-01T20:00:00Z", "2026-11-01T23:00:00Z")
        assertEquals(SportsAction.Upcoming(ahead), sportsAction(ahead, now))
        assertEquals(SportsAction.None, sportsAction(event, Instant.parse(event.stop)))
        assertEquals(SportsAction.None, sportsAction(event.copy(feeds = emptyList()), now))
    }

    @Test fun aFeedChoiceIsNotAudioConsentAndNeverAChannelTheGameDoesNotList() {
        val several = event.copy(feeds = listOf(feed(7), feed(8)))
        val id = sportsFeedPlaybackId(several, "ch:s:8", now)!!
        val request = PlaybackRequest(id)
        assertEquals("ch:s:8", request.id)
        assertFalse(request.allowUnverifiedAudio)
        assertNull(request.variantId)
        assertNull(sportsFeedPlaybackId(several, "ch:other:7", now))
        assertNull(sportsFeedPlaybackId(several, "ch:s:8", Instant.parse(event.stop)))
        assertEquals("ch:s:301", sportsFeedPlaybackId(gameFeed, "ch:s:301", now))
    }

    @Test fun anUpcomingGameIsRecordedByTheProgrammeTheServerNamedForItsFirstChannel() {
        val ahead = event.copy(title = "Rovers at United", away = "Rovers", home = "United", feeds = listOf(feed(7), feed(8)))
        assertEquals(
            Programme(id = "prog:7", channelId = "ch:s:7", start = ahead.start, stop = ahead.stop, title = "Rovers at United", isSport = true, teams = listOf("Rovers", "United")),
            sportsProgramme(ahead),
        )
        assertEquals("scheduled", sportsProgramme(ahead, "scheduled")?.recordingState)
        assertNull(sportsProgramme(ahead.copy(feeds = emptyList())))
        // "A vs B": A is home, and the title names it first.
        assertEquals(listOf("United", "Rovers"), sportsSides(ahead.copy(title = "United vs Rovers")))
        assertEquals(emptyList<String>(), sportsSides(event))
    }

    @Test fun feedAndNetworkLinesNeverShowARawLanguageCodeOrAPlaceholder() {
        assertEquals("Ch 8 · HD · Spanish", sportsFeedDetail(feed(8, "HD").copy(language = "spa"), Locale.ENGLISH))
        assertEquals("Ch 8", sportsFeedDetail(feed(8).copy(language = "und"), Locale.ENGLISH))
        assertNull(sportsFeedDetail(feed(0), Locale.ENGLISH))
        val network = SportsChannel("ch:s:50", "Sports One", 50)
        assertEquals("Ch 50", sportsNetworkLine(network))
        assertEquals("Ch 50 · Now: Match Day", sportsNetworkLine(network.copy(now = Programme("p", "ch:s:50", "", "", "Match Day"))))
        assertEquals("My languages", sportsLanguageLabel(null))
        assertEquals("All languages", sportsLanguageLabel("all"))
        assertEquals("French", sportsLanguageLabel("fre", Locale.ENGLISH))
    }

    @Test fun theQueryAsksForWhatIsNotOverOnTheViewersOwnDays() {
        val params = sportsEventParameters(NativeSportsFilters(language = "eng", sport = "Soccer", date = "ignored", status = "current", query = "  united  "), "2026-11-01", 2, "America/Chicago", 100)
        assertEquals(mapOf("language" to "eng", "sport" to "Soccer", "q" to "united", "date" to "2026-11-01", "timezone" to "America/Chicago", "days" to "2", "status" to "active", "offset" to "100", "limit" to "100"), params)
        assertEquals("7", sportsEventParameters(NativeSportsFilters(), "2026-11-01", 30, "UTC", -5, 500)["days"])
        assertEquals("0", sportsEventParameters(NativeSportsFilters(), "2026-11-01", 1, "UTC", -5, 500)["offset"])
        assertEquals("100", sportsEventParameters(NativeSportsFilters(), "2026-11-01", 1, "UTC", 0, 500)["limit"])
    }

    @Test fun theContractsNewFieldsDecodeAndOlderAnswersStillDo() {
        val json = Json { ignoreUnknownKeys = true }
        val page = json.decodeFromString<SportsEventsPage>("""{"events":[{"id":"e","kind":"channel","title":"Rovers at United","sport":"Soccer","league":"LEAGUE PASS","home":"United","away":"Rovers","start":"2026-11-01T06:00:00Z","stop":"2026-11-02T06:00:00Z","status":"unscheduled","replay":false,"live_evidence":false,"grouping_evidence":"event_slot","feeds":[{"programme_id":"prog:slot-4-1","channel_id":"ch:source:7","channel_name":"LEAGUE PASS 001","channel_number":301,"quality":"FHD","source_id":"source","source_name":"IPTV","language":null,"language_verified":false}]}],"truncated":true}""")
        val decoded = page.events.single()
        assertTrue(page.truncated)
        assertTrue(decoded.isGameFeed())
        assertEquals("LEAGUE PASS", decoded.league)
        assertEquals(301, decoded.feeds.single().channelNumber)
        assertEquals("FHD", decoded.feeds.single().quality)
        assertNull(decoded.feeds.single().language)
        val old = json.decodeFromString<SportsEventsPage>("""{"events":[{"id":"e","title":"EPG event","sport":"Soccer","start":"2026-11-01T18:00:00Z","stop":"2026-11-01T21:00:00Z","status":"current","feeds":[{"programme_id":"prog:4","channel_id":"ch:source:7","channel_name":"Feed 7","source_id":"source","source_name":"IPTV","language_verified":false}]}]}""")
        assertEquals(SportsKinds.EVENT, old.events.single().kind)
        assertNull(old.events.single().feeds.single().channelNumber)
        val networks = json.decodeFromString<SportsChannelsPage>("""{"channels":[{"channel_id":"ch:s:50","channel_name":"Sports One","channel_number":50,"logo":null,"group":"Sports","quality":"HD","language":"eng","source_id":"s","now":null}],"total":31,"limit":24,"offset":0,"truncated":false}""")
        assertNull(networks.channels.single().now)
        assertEquals(31, networks.total)
    }
}
