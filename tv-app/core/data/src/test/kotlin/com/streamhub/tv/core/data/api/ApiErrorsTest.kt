package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.model.ActiveStream
import com.streamhub.tv.core.data.model.ApiError
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
}
