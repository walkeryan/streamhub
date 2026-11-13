package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.MediaKind
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

    @Test fun anOpenRequestIsOneStillOnItsWay() {
        for (state in listOf("pending_approval", "approved", "sent")) assertTrue(state, isOpenRequest(state))
        for (state in listOf("available", "declined", "failed", null, "something_new")) assertFalse("$state", isOpenRequest(state))
    }

    @Test fun everyStateHasALabelAndASentence() {
        for (state in listOf("pending_approval", "approved", "sent", "available", "declined", "failed")) {
            assertTrue(state, requestStateLabel(state)!!.isNotBlank())
            assertTrue(state, requestStateText(state)!!.endsWith("."))
        }
        assertEquals("Requested", requestStateLabel("pending_approval"))
        assertEquals("Requested. Waiting for approval.", requestStateText("pending_approval"))
        assertNull(requestStateLabel(null))
        assertNull(requestStateText(null))
        assertEquals("On hold", requestStateLabel("on_hold"))
    }

    @Test fun searchTilesWithNoInAppSourceSayWhereTheyStand() {
        assertNull(availabilityBadge(tile(inApp = true)))
        assertEquals("Not available", availabilityBadge(tile(inApp = null)))
        // A hand-off source is still not something this app can play.
        assertEquals("Not available", availabilityBadge(tile(inApp = false)))
        assertEquals("Requested", availabilityBadge(tile(inApp = null, state = "pending_approval")))
        assertEquals("Declined", availabilityBadge(tile(inApp = false, state = "declined")))
        // A request that was fulfilled and now plays here needs no badge.
        assertNull(availabilityBadge(tile(inApp = true, state = "available")))
        assertNull(availabilityBadge(tile(kind = MediaKind.CHANNEL)))
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
        val t = StreamHubJson.decodeFromString(Tile.serializer(), """{"id":"tmdb:movie:603","kind":"movie","title":"x","sources":[],"request_state":"sent","requestable":true}""")
        assertEquals("sent", t.requestState)
        assertTrue(t.requestable)
        val old = StreamHubJson.decodeFromString(Tile.serializer(), """{"id":"tmdb:movie:603","kind":"movie","title":"x","sources":[],"request_state":null}""")
        assertNull(old.requestState)
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
