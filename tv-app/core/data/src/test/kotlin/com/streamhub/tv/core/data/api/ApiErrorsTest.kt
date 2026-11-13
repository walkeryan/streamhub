package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.model.ActiveStream
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.repo.SessionEnd
import kotlinx.serialization.SerializationException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.SocketTimeoutException
import java.nio.channels.UnresolvedAddressException

class ApiErrorsTest {
    private fun api(status: Int, code: String? = null, message: String = "raw server text", active: List<ActiveStream> = emptyList()) =
        ApiException(status, code?.let { ApiError(it, message, active) }, message)

    @Test fun sourceBusyNamesWhoIsWatching() {
        val e = ApiErrors.describe(api(409, "source_busy", active = listOf(ActiveStream("Kids", "Evening News"), ActiveStream("Guest", "The Long Film"))))
        assertEquals("Both streams are in use. Kids is watching Evening News. Guest is watching The Long Film.", e.body)
        assertTrue(e.canRetry)
    }

    @Test fun sourceBusyWithOneStreamAndAnUnnamedProfile() {
        val e = ApiErrors.describe(api(409, "source_busy", active = listOf(ActiveStream(null, "Evening News"))))
        assertEquals("The only stream is in use. Someone is watching Evening News.", e.body)
    }

    @Test fun sourceBusyWithoutTheListStaysGeneric() {
        val e = ApiErrors.describe(api(409, "source_busy"))
        assertEquals("No free stream", e.title)
        assertFalse(e.body.contains("raw server text"))
        assertTrue(e.body.contains("in use"))
        assertTrue(e.canRetry)
        // Entries without a title cannot be worded and fall back to the same text.
        assertEquals(e.body, ApiErrors.describe(api(409, "source_busy", active = listOf(ActiveStream("Kids", null)))).body)
    }

    @Test fun sourceBusyListDecodesFromTheWire() {
        val err = StreamHubJson.decodeFromString(ApiError.serializer(),
            """{"code":"source_busy","message":"Source connection limit is occupied.","active":[{"profile_name":"Kids","title":"Evening News","since":"ignored"}]}""")
        assertEquals(listOf(ActiveStream("Kids", "Evening News")), err.active)
        // Servers that do not send the list yet, or send a bare body, still decode.
        assertEquals(emptyList<ActiveStream>(), StreamHubJson.decodeFromString(ApiError.serializer(), """{"code":"source_busy","message":"x"}""").active)
        assertEquals("", StreamHubJson.decodeFromString(ApiError.serializer(), "{}").code)
    }

    @Test fun sourceLimitReached() {
        val e = ApiErrors.describe(api(409, "source_limit_reached"))
        assertEquals("Stream limit reached", e.title)
        assertTrue(e.canRetry)
    }

    @Test fun audioSelectionRequiredOffersRetry() {
        val e = ApiErrors.describe(api(409, "audio_selection_required"))
        assertEquals("Choose an audio source", e.title)
        assertTrue(e.canRetry)
    }

    @Test fun disclaimerRequiredCannotBeRetried() {
        val e = ApiErrors.describe(api(412, "disclaimer_required"))
        assertEquals("Legal notice not accepted", e.title)
        assertFalse(e.canRetry)
    }

    @Test fun unauthorizedAndForbidden() {
        assertEquals("Sign-in expired", ApiErrors.describe(api(401, "unauthorized")).title)
        assertEquals("Not allowed", ApiErrors.describe(api(403, "forbidden")).title)
        assertFalse(ApiErrors.describe(api(401)).canRetry)
        assertFalse(ApiErrors.describe(api(403)).canRetry)
    }

    @Test fun notFound() {
        val e = ApiErrors.describe(api(404, "not_found"))
        assertEquals("No longer available", e.title)
        assertFalse(e.canRetry)
    }

    @Test fun serverErrorsAreRetryableAndNameTheStatus() {
        for (status in listOf(500, 502, 503)) {
            val e = ApiErrors.describe(api(status, "upstream_error"))
            assertEquals("Server problem", e.title)
            assertTrue(e.body.contains("error $status"))
            assertTrue(e.canRetry)
        }
        // A proxy's HTML error page has no decodable body.
        assertTrue(ApiErrors.describe(api(502)).canRetry)
    }

    @Test fun networkFailuresAreRetryable() {
        for (failure in listOf(IOException("Connection refused"), SocketTimeoutException("timeout"), UnresolvedAddressException())) {
            val e = ApiErrors.describe(failure)
            assertEquals("Can't reach the server", e.title)
            assertTrue(e.canRetry)
            assertFalse(e.body.contains("Connection refused"))
        }
    }

    @Test fun otherClientErrorsShowTheServerMessage() {
        val e = ApiErrors.describe(api(409, "conflict", "Previous source connection is still closing."))
        assertEquals("Previous source connection is still closing.", e.body)
        assertTrue(e.canRetry)
        assertFalse(ApiErrors.describe(api(410, "expired")).canRetry)
        assertEquals("The server refused the request (error 422).", ApiErrors.describe(api(422, "bad_request", " ")).body)
    }

    @Test fun missingServerAddressAndUnknownFailures() {
        assertEquals("No server set", ApiErrors.describe(ApiException(0, null, "Server URL not set")).title)
        assertEquals("Unexpected answer from the server", ApiErrors.describe(SerializationException("bad json")).title)
        val unknown = ApiErrors.describe(IllegalStateException("boom"))
        assertEquals("Something went wrong", unknown.title)
        assertEquals("boom", unknown.body)
        assertEquals("Try again in a moment.", ApiErrors.describe(IllegalStateException()).body)
    }

    @Test fun forbiddenSaysSoAndNamesWhatIsMissing() {
        val plain = ApiErrors.describe(api(403, "forbidden"))
        assertEquals("You don't have permission for this.", plain.body)
        assertFalse(plain.canRetry)
        val named = ApiErrors.describe(ApiException(403, ApiError("forbidden", "You do not have permission to do this", missing = listOf("recordings.schedule", "rooms.create")), "x"))
        assertEquals("You don't have permission for this. Ask the owner to let you schedule recordings, start watch rooms.", named.body)
        // A permission this build has no wording for is still named.
        assertTrue(ApiErrors.describe(ApiException(403, ApiError("forbidden", "", missing = listOf("future.thing")), "x")).body.contains("future.thing"))
    }

    @Test fun forbiddenMissingListDecodesFromTheWire() {
        val err = StreamHubJson.decodeFromString(ApiError.serializer(), """{"code":"forbidden","message":"no","missing":["rooms.join"]}""")
        assertEquals(listOf("rooms.join"), err.missing)
        assertEquals(emptyList<String>(), StreamHubJson.decodeFromString(ApiError.serializer(), """{"code":"forbidden","message":"no"}""").missing)
    }

    @Test fun suspendedAndPendingAccountsAreExplained() {
        val suspended = ApiErrors.describe(api(401, "user_suspended"))
        assertEquals("Account suspended", suspended.title)
        assertFalse(suspended.canRetry)
        // The users service answers the same code with 403.
        assertEquals("Account suspended", ApiErrors.describe(api(403, "user_suspended")).title)
        val pending = ApiErrors.describe(api(403, "approval_pending"))
        assertEquals("Waiting for approval", pending.title)
        assertTrue(pending.canRetry)
    }

    @Test fun userStreamLimitNamesWhatIsPlayingElsewhere() {
        val one = ApiErrors.describe(api(409, "user_stream_limit", active = listOf(ActiveStream("Sam", "The Long Film"))))
        assertEquals("Already watching", one.title)
        assertEquals("You're already watching The Long Film on another device. Stop it there, then try again.", one.body)
        assertTrue(one.canRetry)
        val two = ApiErrors.describe(api(409, "user_stream_limit", active = listOf(ActiveStream(null, "Evening News"), ActiveStream(null, "The Long Film"))))
        assertEquals("You're already watching Evening News and The Long Film on other devices. Stop one of them, then try again.", two.body)
        val unnamed = ApiErrors.describe(api(409, "user_stream_limit", active = listOf(ActiveStream("Sam", null))))
        assertTrue(unnamed.body.startsWith("You're already watching on as many devices"))
        assertFalse(unnamed.body.contains("raw server text"))
    }

    @Test fun staleSessionThatSurvivedTheSilentRefreshCanBeRetried() {
        val e = ApiErrors.describe(api(401, "session_stale"))
        assertEquals("Access updated", e.title)
        assertTrue(e.canRetry)
    }

    @Test fun aRefusedRecordingIsExplainedInTheServersWords() {
        // Above the viewer's rating ceiling, or gone: whatever the server says about it is what is shown.
        val ceiling = ApiErrors.describeRecordingPlayback(api(403, "content_restricted", message = "this recording is rated above what this account may watch"))
        assertEquals("Can't play this recording", ceiling.title)
        assertEquals("This recording is rated above what this account may watch.", ceiling.body)
        assertFalse(ceiling.canRetry)
        val gone = ApiErrors.describeRecordingPlayback(api(404, "not_found", message = "recording not found"))
        assertEquals("Recording not found.", gone.body)
        assertEquals("Already a sentence!", ApiErrors.describeRecordingPlayback(api(409, "conflict", message = "Already a sentence!")).body)
    }

    @Test fun refusalsThatAreNotAboutTheRecordingKeepTheirOwnWording() {
        val limit = ApiErrors.describeRecordingPlayback(api(409, "user_stream_limit", active = listOf(ActiveStream(null, "The Long Film"))))
        assertEquals("You're already watching The Long Film on another device. Stop it there, then try again.", limit.body)
        assertEquals("Account suspended", ApiErrors.describeRecordingPlayback(api(401, "user_suspended")).title)
        assertEquals("Not allowed", ApiErrors.describeRecordingPlayback(api(403, "forbidden")).title)
        // Nothing said, a server fault, or no answer at all: the general wording.
        assertEquals(ApiErrors.describe(api(404, "not_found", message = " ")), ApiErrors.describeRecordingPlayback(api(404, "not_found", message = " ")))
        assertEquals("Server problem", ApiErrors.describeRecordingPlayback(api(503, "upstream_error")).title)
        assertEquals("Can't reach the server", ApiErrors.describeRecordingPlayback(IOException("down")).title)
    }

    @Test fun anEndedSessionSaysWhichKindOfEndItWas() {
        val revoked = ApiErrors.sessionEnded(SessionEnd.Revoked)
        assertEquals("Signed out", revoked.title)
        assertTrue(revoked.body.contains("Pair it again"))
        assertEquals(revoked, ApiErrors.describe(api(401, "session_revoked")))
        assertEquals("Sign-in expired", ApiErrors.sessionEnded(SessionEnd.Expired).title)
        // A suspension is not a reason to pair again: the pairing is kept.
        val suspended = ApiErrors.sessionEnded(SessionEnd.Suspended)
        assertEquals(suspended, ApiErrors.describe(api(401, "user_suspended")))
        assertFalse(suspended.body.contains("Pair"))
        for (end in SessionEnd.entries) assertFalse("$end", ApiErrors.sessionEnded(end).canRetry)
    }

    @Test fun onlyRevocationAndSuspensionEndASession() {
        assertEquals(SessionEnd.Revoked, SessionEnd.ofCode("session_revoked"))
        assertEquals(SessionEnd.Suspended, SessionEnd.ofCode("user_suspended"))
        // Refreshed and repeated, or about something else altogether.
        for (code in listOf("session_stale", "token_expired", "pin_invalid", "pin_required", "unauthorized", "", null)) assertEquals("$code", null, SessionEnd.ofCode(code))
    }

    @Test fun awayFromHomeIsSaidOnAnyCall() {
        val e = ApiErrors.describe(api(403, "remote_access_denied"))
        assertEquals("Home network only", e.title)
        assertTrue(e.body.startsWith("This account can only be used on the home network."))
        assertFalse(e.body.contains("raw server text"))
    }
}
