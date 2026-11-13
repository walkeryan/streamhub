package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.MediaRequest
import com.streamhub.tv.core.data.model.RequestProgress
import com.streamhub.tv.core.data.model.TileRequestProgress
import com.streamhub.tv.core.data.model.NoticesResponse
import com.streamhub.tv.core.data.model.RequestCreate
import com.streamhub.tv.core.data.model.RequestsResponse
import com.streamhub.tv.core.data.model.SourceBadge
import com.streamhub.tv.core.data.model.SourceType
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.data.model.TitleDetail
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RequestRulesTest {
    private fun tile(kind: MediaKind = MediaKind.MOVIE, inApp: Boolean? = null, state: String? = null) = Tile(
        id = "tmdb:movie:603", kind = kind, title = "The Long Film", requestState = state,
        sources = inApp?.let { listOf(SourceBadge("s1", if (it) SourceType.PLEX else SourceType.STREAMING_SERVICE, "Source", playableInApp = it)) }.orEmpty(),
    )

    @Test fun aRequestNamesTheTitleByItsTmdbId() {
        assertEquals(RequestTarget("movie", 603), requestTarget("tmdb:movie:603"))
        assertEquals(RequestTarget("tv", 1399), requestTarget("tmdb:tv:1399"))
    }

    @Test fun titlesWithoutATmdbIdCannotBeRequested() {
        for (id in listOf("lib:s1:42", "ch:s1:7", "tmdb:movie:", "tmdb:movie:abc", "tmdb:person:5", "tmdb:movie:0", "tmdb:movie:603:extra", "")) {
            assertNull(id, requestTarget(id))
        }
    }

    @Test fun anOpenRequestIsOneNotYetClosed() {
        for (state in listOf("pending_approval", "approved", "sent")) assertTrue(state, isOpenRequest(state))
        for (state in listOf("available", "declined", "failed", null, "something_new")) assertFalse("$state", isOpenRequest(state))
    }

    @Test fun everyPhaseHasItsOwnChip() {
        val chips = mapOf(
            "requested" to "Requested", "approved" to "Approved", "searching" to "Searching", "downloading" to "Downloading", "importing" to "Importing",
            "partially_available" to "Partly available", "available" to "Available", "declined" to "Declined", "failed" to "Failed", "stalled" to "Stalled", "unknown" to "Status unknown",
        )
        for ((phase, label) in chips) assertEquals(label, requestStatus(RequestProgress(phase)).label)
        assertEquals(chips.size, chips.values.toSet().size)
        // "On its way" said nothing about what was happening: no phase reads that way any more.
        assertFalse(chips.values.any { it.contains("way", ignoreCase = true) })
        assertNull(requestPhaseLabel(null))
        // A phase from a newer server is shown by its own name rather than hidden.
        assertEquals("On hold", requestPhaseLabel("on_hold"))
    }

    @Test fun aServerWithoutProgressIsReadFromTheRequestsState() {
        assertEquals("requested", phaseOfState("pending_approval"))
        assertEquals("searching", phaseOfState("sent"))
        assertEquals("approved", phaseOfState("approved"))
        assertNull(phaseOfState(null))
        assertEquals("Searching", requestStateLabel("sent"))
        assertEquals("Requested", requestStateLabel("pending_approval"))
        val old = MediaRequest(id = "q", state = "declined", note = "Too long")
        assertEquals(RequestProgress("declined", detail = "Too long"), old.progressOrState())
        assertEquals(RequestProgress("searching"), MediaRequest(id = "q", state = "sent", note = "queued upstream").progressOrState())
        val reported = MediaRequest(id = "q", state = "sent", progress = RequestProgress("downloading", percent = 12))
        assertEquals(reported.progress, reported.progressOrState())
    }

    @Test fun goodNewsAndTroubleAreToldApart() {
        assertEquals(RequestTone.Ok, requestTone("available"))
        for (phase in listOf("failed", "declined", "stalled")) assertEquals(phase, RequestTone.Warn, requestTone(phase))
        for (phase in listOf("requested", "approved", "searching", "downloading", "importing", "partially_available", "unknown")) assertEquals(phase, RequestTone.Plain, requestTone(phase))
    }

    @Test fun aDownloadShowsABarWithThePercentAndTheTimeLeft() {
        val status = requestStatus(RequestProgress("downloading", percent = 42, eta = "2026-11-13T20:12:00Z", checkedAt = "2026-11-13T20:00:00Z", detail = "2 of 8 episodes"))
        assertEquals("Downloading", status.label)
        assertEquals(RequestBar(42), status.bar)
        assertEquals(0.42f, status.bar!!.fraction!!, 0.0001f)
        assertEquals("42% · about 12 min left", status.caption)
        assertEquals("2 of 8 episodes", status.secondary)
    }

    @Test fun aDownloadWithoutSizesHasAnIndeterminateBar() {
        val status = requestStatus(RequestProgress("downloading", percent = null))
        assertEquals(RequestBar(null), status.bar)
        assertNull(status.bar!!.fraction)
        assertNull(status.caption)
        assertEquals("about 3 min left", requestStatus(RequestProgress("downloading", eta = "2026-11-13T20:03:00Z", checkedAt = "2026-11-13T20:00:00Z")).caption)
    }

    @Test fun onlyADownloadHasABar() {
        for (phase in listOf("requested", "approved", "searching", "importing", "partially_available", "available", "declined", "failed", "stalled", "unknown")) {
            val status = requestStatus(RequestProgress(phase, percent = 50, eta = "2026-11-13T20:12:00Z", checkedAt = "2026-11-13T20:00:00Z"))
            assertNull(phase, status.bar)
            assertNull(phase, status.caption)
        }
        assertEquals(100, requestStatus(RequestProgress("downloading", percent = 140)).bar!!.percent)
    }

    @Test fun theTimeLeftIsCountedFromTheServersOwnCheck() {
        assertEquals("less than a minute left", requestEtaText("2026-11-13T20:00:20Z", "2026-11-13T20:00:00Z"))
        assertEquals("about 59 min left", requestEtaText("2026-11-13T20:59:00Z", "2026-11-13T20:00:00Z"))
        assertEquals("about 1 hr left", requestEtaText("2026-11-13T21:00:00Z", "2026-11-13T20:00:00Z"))
        assertEquals("about 2 hr 5 min left", requestEtaText("2026-11-13T22:05:00+00:00", "2026-11-13T20:00:00Z"))
        assertNull("a time that has passed", requestEtaText("2026-11-13T19:00:00Z", "2026-11-13T20:00:00Z"))
        assertNull(requestEtaText(null, "2026-11-13T20:00:00Z"))
        assertNull(requestEtaText("2026-11-13T21:00:00Z", null))
        assertNull(requestEtaText("soon", "now"))
    }

    @Test fun anUnknownStatusSaysWhatWasLastKnown() {
        val status = requestStatus(RequestProgress("unknown", lastPhase = "downloading", detail = "No answer"))
        assertEquals("Status unknown", status.label)
        assertEquals("No answer Last known: downloading.", status.secondary)
        assertNull(requestStatus(RequestProgress("searching", lastPhase = "downloading")).secondary)
    }

    @Test fun aPosterShowsARequestInProgressAndNothingForOldRefusals() {
        assertNull(tileRequest(tile(inApp = true)))
        assertNull(tileRequest(tile(inApp = null)))
        // A hand-off source is still not something this app can play, and it is not a request either.
        assertNull(tileRequest(tile(inApp = false)))
        assertEquals(TileRequestProgress("requested"), tileRequest(tile(inApp = null, state = "pending_approval")))
        assertEquals(TileRequestProgress("searching"), tileRequest(tile(inApp = false, state = "sent")))
        assertNull(tileRequest(tile(inApp = false, state = "declined")))
        assertNull(tileRequest(tile(inApp = null, state = "failed")))
        // Arrived, but the library does not have the title yet: said. Once it plays here, nothing.
        assertEquals(TileRequestProgress("available"), tileRequest(tile(inApp = null, state = "available")))
        assertNull(tileRequest(tile(inApp = true, state = "available")))
        assertNull(tileRequest(tile(kind = MediaKind.CHANNEL, state = "sent")))
        val downloading = tile(state = "sent").copy(requestProgress = TileRequestProgress("downloading", 42))
        assertEquals("Downloading 42%", requestStatus(tileRequest(downloading)!!).label)
        assertEquals(RequestBar(42), requestStatus(tileRequest(downloading)!!).bar)
        assertEquals("Downloading", requestStatus(TileRequestProgress("downloading")).label)
        // The declined request is still told on the title's own page.
        assertEquals(TileRequestProgress("declined"), requestOf(tile(state = "declined")))
    }

    @Test fun aScreenAsksAgainEvery15SecondsWhileSomethingMovesAndEvery3MinutesOtherwise() {
        assertEquals(15_000L, requestPollMs(listOf("searching", "downloading")))
        assertEquals(15_000L, requestPollMs(listOf("importing")))
        assertEquals(180_000L, requestPollMs(listOf("requested", "approved", "searching", "stalled", "unknown", "available", null)))
        assertEquals(180_000L, requestPollMs(emptyList()))
    }

    @Test fun requestsAreListedByActivityAndCheckedAgainOnlyWhereItCanHelp() {
        fun request(id: String, state: String, phase: String? = null) = MediaRequest(id = id, state = state, progress = phase?.let { RequestProgress(it) })
        val listed = byActivity(listOf(
            request("declined", "declined"), request("waiting", "pending_approval"), request("stalled", "sent", "stalled"),
            request("down", "sent", "downloading"), request("searching", "sent", "searching"), request("import", "sent", "importing"), request("done", "available", "available"),
        ))
        assertEquals(listOf("down", "import", "stalled", "waiting", "searching", "declined", "done"), listed.map { it.id })
        assertTrue(canCheckAgain(request("a", "sent", "downloading")))
        assertTrue(canCheckAgain(request("a", "approved")))
        assertTrue(canCheckAgain(request("a", "available", "unknown")))
        assertFalse(canCheckAgain(request("a", "pending_approval")))
        assertFalse(canCheckAgain(request("a", "declined", "declined")))
    }

    @Test fun seasonsCanBePickedOnlyForASeriesThatListsThem() {
        assertTrue(canPickSeasons(RequestTarget("tv", 1), listOf(1, 2, 3)))
        assertFalse(canPickSeasons(RequestTarget("tv", 1), emptyList()))
        assertFalse(canPickSeasons(RequestTarget("movie", 1), listOf(1)))
        assertFalse(canPickSeasons(null, listOf(1)))
    }

    @Test fun aWholeSeriesRequestLeavesSeasonsOut() {
        assertEquals("""{"media_type":"tv","tmdb_id":1399}""", StreamHubJson.encodeToString(RequestCreate.serializer(), RequestCreate("tv", 1399)))
        assertEquals("""{"media_type":"tv","tmdb_id":1399,"seasons":[1,3]}""", StreamHubJson.encodeToString(RequestCreate.serializer(), RequestCreate("tv", 1399, listOf(1, 3))))
    }

    @Test fun refusalsAreWorded() {
        fun refusal(code: String) = ApiErrors.describe(ApiException(409, ApiError(code, "server text"), "server text"))
        assertEquals("Already available", refusal("already_available").title)
        assertEquals("Requests aren't set up", refusal("request_manager_unconfigured").title)
        assertTrue(refusal("metadata_unavailable").canRetry)
    }

    @Test fun requestFieldsDecodeFromTheWire() {
        val t = StreamHubJson.decodeFromString(Tile.serializer(), """{"id":"tmdb:movie:603","kind":"movie","title":"x","sources":[],"request_state":"sent","request_progress":{"phase":"downloading","percent":42},"requestable":true}""")
        assertEquals("sent", t.requestState)
        assertEquals(TileRequestProgress("downloading", 42), t.requestProgress)
        assertTrue(t.requestable)
        val full = StreamHubJson.decodeFromString(MediaRequest.serializer(), """{"id":"q1","profile_id":"p1","media_type":"movie","tmdb_id":603,"title_id":"tmdb:movie:603","title":"x","state":"sent",
            "progress":{"phase":"unknown","last_phase":"downloading","percent":null,"eta":null,"detail":"No answer","checked_at":"2026-11-13T20:00:00Z"},"created_at":"x","updated_at":"y"}""")
        assertEquals(RequestProgress("unknown", lastPhase = "downloading", detail = "No answer", checkedAt = "2026-11-13T20:00:00Z"), full.progress)
        val newer = StreamHubJson.decodeFromString(MediaRequest.serializer(), """{"id":"q1","state":"sent","progress":{"phase":"verifying"}}""")
        assertEquals("Verifying", requestStatus(newer.progressOrState()).label)
        val old = StreamHubJson.decodeFromString(Tile.serializer(), """{"id":"tmdb:movie:603","kind":"movie","title":"x","sources":[],"request_state":null}""")
        assertNull(old.requestState)
        assertNull(old.requestProgress)
        assertFalse(old.requestable)
        val d = StreamHubJson.decodeFromString(TitleDetail.serializer(), """{"id":"tmdb:tv:1399","kind":"show","title":"x","sources":[],"requestable":true,"request_seasons":[1,2,3]}""")
        assertEquals(listOf(1, 2, 3), d.requestSeasons)
        assertTrue(d.asTile().requestable)
        val list = StreamHubJson.decodeFromString(RequestsResponse.serializer(), """
            {"requests":[{"id":"q1","profile_id":"p1","profile_name":"Sam","media_type":"tv","tmdb_id":1399,"title_id":"tmdb:tv:1399","title":"The Long Show",
              "year":2011,"poster":null,"seasons":[1,2],"state":"declined","note":"Too long","decided_by_profile_id":"p0","created_at":"x","updated_at":"y"}],"can_approve":true}
        """.trimIndent())
        assertEquals("Too long", list.requests[0].note)
        assertEquals(listOf(1, 2), list.requests[0].seasons)
        assertEquals(true, list.canApprove)
        assertNull(StreamHubJson.decodeFromString(RequestsResponse.serializer(), """{"requests":[]}""").canApprove)
        val notices = StreamHubJson.decodeFromString(NoticesResponse.serializer(), """{"notices":[{"id":"n1","kind":"request_available","title":"The Long Film is ready","body":"x","ref_id":"tmdb:movie:603","created_at":"x","read_at":null},{"id":"n2","kind":"k","title":"t","created_at":"x","read_at":"2026-11-13T10:00:00Z"}]}""")
        assertNull(notices.notices[0].readAt)
        assertEquals("tmdb:movie:603", notices.notices[0].refId)
        assertEquals("2026-11-13T10:00:00Z", notices.notices[1].readAt)
    }
}
