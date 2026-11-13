package com.streamhub.tv.core.data.api

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** What the player sees when the server refuses a media URL: an HTTP status and the usual error body. */
class StreamRefusalsTest {
    private fun body(code: String, message: String = "server text", extra: String = "") = """{"code":"$code","message":"$message"$extra}""".toByteArray()

    @Test fun everyRefusalOfAMediaUrlIsRead() {
        val cases = listOf(401 to "session_revoked", 401 to "user_suspended", 401 to "session_stale", 403 to "remote_access_denied", 403 to "content_restricted", 409 to "user_stream_limit")
        for ((status, code) in cases) {
            val e = StreamRefusals.parse(status, body(code))
            assertNotNull("$status $code", e)
            assertEquals(status, e!!.status)
            assertEquals(code, e.error?.code)
        }
    }

    @Test fun theStreamLimitNamesWhatIsPlayingElsewhere() {
        val e = StreamRefusals.parse(409, body("user_stream_limit", extra = ""","active":[{"profile_name":null,"title":"The Long Film","kind":"movie","since":"2026-11-13T20:00:00Z"}],"limit":1"""))!!
        val shown = ApiErrors.describe(e)
        assertEquals("Already watching", shown.title)
        assertEquals("You're already watching The Long Film on another device. Stop it there, then try again.", shown.body)
        assertTrue(shown.canRetry)
        // Two streams in the way, and none the server may name.
        val two = StreamRefusals.parse(409, body("user_stream_limit", extra = ""","active":[{"title":"The Long Film"},{"title":"Evening News"}]"""))!!
        assertEquals("You're already watching The Long Film and Evening News on other devices. Stop one of them, then try again.", ApiErrors.describe(two).body)
        val unnamed = StreamRefusals.parse(409, body("user_stream_limit", extra = ""","active":[{"title":null}]"""))!!
        assertTrue(ApiErrors.describe(unnamed).body.startsWith("You're already watching on as many devices"))
    }

    @Test fun awayFromHomeSaysTheAccountIsForTheHomeNetwork() {
        val shown = ApiErrors.describe(StreamRefusals.parse(403, body("remote_access_denied"))!!)
        assertEquals("Home network only", shown.title)
        assertTrue(shown.body.startsWith("This account can only be used on the home network."))
        assertFalse(shown.body.contains("server text"))
        // The same refusal of an ordinary call reads the same.
        assertEquals(shown, ApiErrors.describe(ApiException(403, com.streamhub.tv.core.data.model.ApiError("remote_access_denied", "x"), "x")))
    }

    @Test fun aChangedRatingCeilingAndAnEndedSessionAreWorded() {
        assertEquals("Not available for this profile", ApiErrors.describe(StreamRefusals.parse(403, body("content_restricted"))!!).title)
        assertEquals("Account suspended", ApiErrors.describe(StreamRefusals.parse(401, body("user_suspended"))!!).title)
        assertEquals("Signed out", ApiErrors.describe(StreamRefusals.parse(401, body("session_revoked"))!!).title)
        val stale = ApiErrors.describe(StreamRefusals.parse(401, body("session_stale"))!!)
        assertEquals("Access updated", stale.title)
        assertTrue(stale.canRetry)
    }

    @Test fun onlyRefusalsAboutTheSessionAreSettledWithTheServerFirst() {
        assertEquals(setOf("session_revoked", "user_suspended", "session_stale"), StreamRefusals.ABOUT_THE_SESSION)
        assertTrue(StreamRefusals.CODES.containsAll(StreamRefusals.ABOUT_THE_SESSION))
        for (code in listOf("remote_access_denied", "content_restricted", "user_stream_limit")) assertFalse(code, code in StreamRefusals.ABOUT_THE_SESSION)
    }

    @Test fun whatIsNotOneOfTheseRefusalsIsLeftToThePlayer() {
        // A busy source keeps reconnecting on a live channel, as before.
        assertNull(StreamRefusals.parse(409, body("source_busy")))
        // A provider's own answer on a direct stream: not JSON, or JSON that is not the server's error.
        assertNull(StreamRefusals.parse(403, "<html>Forbidden</html>".toByteArray()))
        assertNull(StreamRefusals.parse(403, """{"error":"nope"}""".toByteArray()))
        assertNull(StreamRefusals.parse(403, """["session_revoked"]""".toByteArray()))
        assertNull(StreamRefusals.parse(401, ByteArray(0)))
        assertNull(StreamRefusals.parse(401, null))
        // The right code under a status that is not a refusal of the stream.
        assertNull(StreamRefusals.parse(500, body("session_revoked")))
        assertNull(StreamRefusals.parse(200, body("user_stream_limit")))
        assertNull(StreamRefusals.parse(404, body("content_restricted")))
        // Far too large to be an error body.
        assertNull(StreamRefusals.parse(403, body("remote_access_denied", message = "x".repeat(70_000))))
    }

    @Test fun aRefusalWithoutAMessageStillCarriesItsCode() {
        val e = StreamRefusals.parse(401, """{"code":"session_stale"}""".toByteArray())!!
        assertEquals("session_stale", e.error?.code)
        assertEquals("HTTP 401", e.message)
    }
}
