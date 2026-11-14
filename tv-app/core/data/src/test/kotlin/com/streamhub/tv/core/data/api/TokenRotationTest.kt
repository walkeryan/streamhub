package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.SessionTokens
import com.streamhub.tv.core.data.auth.canApplyRefresh
import com.streamhub.tv.core.data.auth.refreshedSession
import com.streamhub.tv.core.data.auth.rotatedSince
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.Profile
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.get
import io.ktor.content.TextContent
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Refresh tokens rotate: every POST /auth/refresh answers a new refresh token, and presenting an
 * older one again ends the session. These run the app's real HTTP client (bearer auth and its
 * refresh) against a scripted server and an in-memory store that applies the same rule as the
 * DataStore one ([refreshedSession]).
 */
class TokenRotationTest {
    private val profile = Profile("p1", "Sam")
    private val base = "https://hub.invalid"

    private class Store(var session: Session) : SessionTokens {
        var saves = 0
        override suspend fun current(): Session = session
        override suspend fun saveRefreshedTokens(expected: Session, tokens: AuthTokens): Boolean {
            session = refreshedSession(expected, session, tokens) ?: return false
            saves++
            return true
        }
        override suspend fun clearExpiredSession(expected: Session): Boolean {
            if (!canApplyRefresh(expected, session, expected.profile?.id)) return false
            session = session.copy(accessToken = null, refreshToken = null, profile = null, generation = "cleared")
            return true
        }
    }

    /** A server whose access tokens are A1, A2, … and refresh tokens R1, R2, …; only the newest of each is good. */
    private inner class Server(private val store: Store) {
        var n = 1
        val presented = mutableListOf<String>()
        /** What the store held when a request carrying a rotated access token arrived. */
        val storedWhenUsed = mutableListOf<String?>()
        var refreshAnswer: (suspend (String) -> Pair<HttpStatusCode, String>)? = null
        fun expire() { n++ }   // the access token ages out; the refresh token is still the newest issued
        private var refreshGeneration = 1
        private val json = headersOf(HttpHeaders.ContentType, ContentType.Application.Json.toString())

        val engine = MockEngine { request ->
            val path = request.url.encodedPath
            if (path.endsWith("/auth/refresh")) {
                assertNull("a refresh never carries a bearer token", request.headers[HttpHeaders.Authorization])
                val token = Regex("\"refresh_token\":\"([^\"]+)\"").find((request.body as TextContent).text)!!.groupValues[1]
                presented += token
                refreshAnswer?.let { answer -> val (status, body) = answer(token); return@MockEngine respond(body, status, json) }
                if (token != "R$refreshGeneration") return@MockEngine respond("""{"code":"session_revoked","message":"replayed"}""", HttpStatusCode.Unauthorized, json)
                refreshGeneration++
                accessGeneration = refreshGeneration
                respond("""{"access_token":"A$refreshGeneration","refresh_token":"R$refreshGeneration","profile":{"id":"p1","name":"Sam"}}""", HttpStatusCode.OK, json)
            } else {
                val bearer = request.headers[HttpHeaders.Authorization]
                if (bearer == "Bearer A$accessGeneration" && !expired) {
                    if (accessGeneration > 1) storedWhenUsed += store.session.refreshToken
                    respond("""{"ok":true}""", HttpStatusCode.OK, json)
                } else { expired = false; respond("""{"code":"token_expired","message":"expired"}""", HttpStatusCode.Unauthorized, json) }
            }
        }
        var accessGeneration = 1
        /** The next API call answers 401 token_expired once, whatever token it carries. */
        var expired = false
    }

    private fun session() = Session(base, "A1", "R1", profile, generation = "g1")

    @Test fun aRefreshStoresTheNewRefreshTokenBeforeTheNewAccessTokenIsUsed() = runTest {
        val store = Store(session())
        val server = Server(store).apply { expired = true }
        var refreshed = 0
        var ended: String? = "none"
        val client = createStreamHubHttpClient(store, debug = false, onSessionExpired = { ended = it }, onTokensRefreshed = { refreshed++ }, engine = server.engine)
        try {
            assertEquals(HttpStatusCode.OK, client.get("$base/api/v1/auth/me").status)
            assertEquals(listOf("R1"), server.presented)
            assertEquals("R2", store.session.refreshToken)
            assertEquals("A2", store.session.accessToken)
            // The retried request carried A2, and R2 was already in the store when it did.
            assertEquals(listOf<String?>("R2"), server.storedWhenUsed)
            assertEquals("the session keeps its identity across a rotation", "g1", store.session.generation)
            assertEquals(1, refreshed)
            assertEquals("none", ended)
        } finally { client.close() }
    }

    @Test fun theNextRefreshPresentsTheRotatedTokenAndNeverAnOlderOne() = runTest {
        val store = Store(session())
        val server = Server(store).apply { expired = true }
        val client = createStreamHubHttpClient(store, debug = false, onSessionExpired = {}, engine = server.engine)
        try {
            client.get("$base/api/v1/home")
            server.expired = true
            assertEquals(HttpStatusCode.OK, client.get("$base/api/v1/home").status)
            server.expired = true
            assertEquals(HttpStatusCode.OK, client.get("$base/api/v1/home").status)
            assertEquals(listOf("R1", "R2", "R3"), server.presented)
            assertEquals("R4", store.session.refreshToken)
            assertEquals(3, store.saves)
        } finally { client.close() }
    }

    @Test fun aReplayedTokenEndsTheSessionAndTheViewerIsTold() = runTest {
        val store = Store(session())
        val server = Server(store).apply {
            expired = true
            refreshAnswer = { HttpStatusCode.Unauthorized to """{"code":"session_revoked","message":"This refresh token was already used."}""" }
        }
        var ended: String? = "none"
        val client = createStreamHubHttpClient(store, debug = false, onSessionExpired = { ended = it }, engine = server.engine)
        try {
            assertEquals(HttpStatusCode.Unauthorized, client.get("$base/api/v1/home").status)
            assertEquals("session_revoked", ended)
            assertNull(store.session.refreshToken)
            assertNull(store.session.accessToken)
            assertEquals("the server address is kept for pairing again", base, store.session.serverUrl)
        } finally { client.close() }
    }

    @Test fun aServerThatCannotAnswerTheRefreshLeavesThePairingAlone() = runTest {
        val store = Store(session())
        val server = Server(store).apply { expired = true; refreshAnswer = { HttpStatusCode.ServiceUnavailable to "restarting" } }
        var ended: String? = "none"
        val client = createStreamHubHttpClient(store, debug = false, onSessionExpired = { ended = it }, engine = server.engine)
        try {
            client.get("$base/api/v1/home")
            assertEquals("none", ended)
            assertEquals("R1", store.session.refreshToken)
            assertEquals(0, store.saves)
        } finally { client.close() }
    }

    @Test fun theRotatedPairReplacesTheStoredOneWhole() {
        val held = session()
        val answer = AuthTokens("A2", "R2", profile.copy(name = "Sam W"))
        val next = refreshedSession(held, held, answer)!!
        assertEquals(Session(base, "A2", "R2", profile.copy(name = "Sam W"), generation = "g1"), next)
        // A session from before generations existed gets a stable one on its first rotation.
        val legacy = held.copy(generation = null)
        val migrated = refreshedSession(legacy, legacy, answer)!!
        assertTrue(migrated.generation!!.isNotBlank())
        assertEquals(migrated.generation, refreshedSession(legacy, legacy, answer.copy(accessToken = "other"))!!.generation)
    }

    @Test fun aLateAnswerNeverOverwritesANewerRotationOrAnotherSession() {
        val held = session()
        val rotated = refreshedSession(held, held, AuthTokens("A2", "R2", profile))!!
        // Two requests refreshed at once and the slower answer arrives second: R2 stays.
        assertNull(refreshedSession(held, rotated, AuthTokens("A2b", "R2b", profile)))
        // The TV was paired again, or handed to someone else, while the refresh was in flight.
        assertNull(refreshedSession(held, held.copy(refreshToken = "other", generation = "g2"), AuthTokens("A2", "R2", profile)))
        assertNull(refreshedSession(held, held.copy(profile = Profile("p2", "Alex")), AuthTokens("A2", "R2", profile)))
        assertNull("an answer for another person is not this session's", refreshedSession(held, held, AuthTokens("A2", "R2", Profile("p2", "Alex"))))
        assertNull(refreshedSession(held, held.copy(serverUrl = "https://other.invalid"), AuthTokens("A2", "R2", profile)))
    }

    @Test fun aRequestThatUsedAnOlderPairTakesTheStoredOneInsteadOfRefreshingAgain() {
        assertTrue(rotatedSince("R1", "R2"))
        assertFalse(rotatedSince("R1", "R1"))
        assertFalse("nothing was loaded yet: the stored token is presented", rotatedSince(null, "R1"))
        assertTrue(rotatedSince("R1", null))
    }

    @Test fun twoRequestsThatExpireTogetherRefreshOnce() = runTest {
        val store = Store(session())
        val server = Server(store)
        val client = createStreamHubHttpClient(store, debug = false, onSessionExpired = {}, engine = server.engine)
        try {
            // Both carry A1 after the server moved on: the first refreshes, the second finds the store rotated.
            client.get("$base/api/v1/home")
            server.accessGeneration = 99 // A1 is no longer good; nothing but a refresh helps
            val first = client.get("$base/api/v1/home")
            assertEquals(listOf("R1"), server.presented)
            assertEquals("R2", store.session.refreshToken)
            assertEquals(HttpStatusCode.OK, first.status)
            assertEquals(HttpStatusCode.OK, client.get("$base/api/v1/channels").status)
            assertEquals("no second refresh for the same expiry", listOf("R1"), server.presented)
        } finally { client.close() }
    }
}
