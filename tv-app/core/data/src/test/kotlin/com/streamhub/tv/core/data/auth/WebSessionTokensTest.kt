package com.streamhub.tv.core.data.auth

import com.streamhub.tv.core.data.api.createStreamHubHttpClient
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.Profile
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.get
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The web app shown by the shell owns the session. The TV hands its pair over once, keeps no
 * refresh token, and gets access tokens by asking the page (a fake page here) through the broker.
 */
class WebSessionTokensTest {
    private val sam = Profile("p1", "Sam", languages = listOf("en"))
    private val base = "http://192.0.2.10:8787"

    // ---- What the page may answer ---------------------------------------------------------------

    @Test fun aTokenAnswerNeedsATokenAndAPerson() {
        val ok = parseWebToken("""{"access":"eyJhbGciOi.abc.def","profile":{"id":"p1","name":"Sam","languages":["en"],"extra":1}}""")
        assertEquals("eyJhbGciOi.abc.def", ok?.access)
        assertEquals("p1", ok?.profile?.id)
        assertNull("no session in the web app", parseWebToken("null"))
        assertNull(parseWebToken(null))
        assertNull(parseWebToken(""))
        assertNull(parseWebToken("[]"))
        assertNull("no person", parseWebToken("""{"access":"eyJhbGciOi.abc.def"}"""))
        assertNull("a number is not a token", parseWebToken("""{"access":12345678,"profile":{"id":"p1","name":"Sam"}}"""))
        assertNull("spaces", parseWebToken("""{"access":"Bearer abc.def.ghi","profile":{"id":"p1","name":"Sam"}}"""))
        assertNull("too short", parseWebToken("""{"access":"abc","profile":{"id":"p1","name":"Sam"}}"""))
        assertNull("blank person", parseWebToken("""{"access":"eyJhbGciOi.abc.def","profile":{"id":"","name":"Sam"}}"""))
        assertNull("not JSON", parseWebToken("access=abc"))
        assertNull("too long", parseWebToken("""{"access":"${"a".repeat(70_000)}","profile":{"id":"p1","name":"Sam"}}"""))
    }

    // ---- The broker: a question, and only its answer --------------------------------------------

    @Test fun aRequestIsAnsweredByThePageThroughTheBroker() = runBlocking {
        val broker = WebTokenBroker(timeoutMs = 5_000)
        val asked = mutableListOf<Pair<String, String?>>()
        // The fake page answers later, from another thread, as the WebView's bridge thread would.
        broker.attach { id, rejected ->
            asked += id to rejected
            CoroutineScope(Dispatchers.Default).launch { broker.deliver(id, """{"access":"token-A2-xyz","profile":{"id":"p1","name":"Sam"}}""") }
            true
        }
        val token = broker.request(rejected = "token-A1-xyz")
        assertEquals("token-A2-xyz", token?.access)
        assertEquals("the refused token is passed to the page", "token-A1-xyz", asked.single().second)
        assertEquals("ids are random and long", 32, asked.single().first.length)
        assertFalse("an id is answered once", broker.deliver(asked.single().first, """{"access":"token-A3-xyz","profile":{"id":"p1","name":"Sam"}}"""))
    }

    @Test fun answersToQuestionsNobodyAskedAreIgnored() = runTest {
        val broker = WebTokenBroker(timeoutMs = 200)
        broker.attach { _, _ -> true }
        assertFalse(broker.deliver("0".repeat(32), """{"access":"token-A2-xyz","profile":{"id":"p1","name":"Sam"}}"""))
        assertNull("no answer in time", broker.request(null))
    }

    @Test fun withoutAPageThereIsNoToken() = runTest {
        val broker = WebTokenBroker(timeoutMs = 200)
        assertNull("nothing attached", broker.request(null))
        broker.attach { _, _ -> false }
        assertNull("the page is not loaded", broker.request(null))
        broker.attach { _, _ -> true }
        broker.detach()
        assertNull(broker.request(null))
    }

    @Test fun aMalformedAnswerIsNoToken() = runBlocking {
        val broker = WebTokenBroker(timeoutMs = 5_000)
        broker.attach { id, _ -> CoroutineScope(Dispatchers.Default).launch { broker.deliver(id, """{"access":"has space in it","profile":{"id":"p1","name":"Sam"}}""") }; true }
        assertNull(broker.request(null))
    }

    // ---- The session: handed over once, then owned by the web app -------------------------------

    @Test fun theShellSeedsAPairThenShowsTheWebApp() {
        assertEquals(ShellEntry.Pair, shellEntry(Session(null, null, null, null)))
        assertEquals(ShellEntry.Pair, shellEntry(Session(base, null, null, null)))
        assertEquals("a pairing just made, or an install from before the shell", ShellEntry.Seed, shellEntry(Session(base, "A1", "R1", sam, "g1")))
        assertEquals(ShellEntry.Web, shellEntry(Session(base, "A1", null, sam, "g1", webOwned = true)))
        assertEquals("signed out", ShellEntry.Pair, shellEntry(Session(base, null, null, null, "g2", webOwned = false)))
    }

    @Test fun handingOverDropsTheRefreshTokenAndKeepsTheSessionsIdentity() {
        val paired = Session(base, "A1", "R1", sam, "g1")
        val handed = handedOverSession(paired, paired)!!
        assertNull("one owner: no refreshing copy stays on the TV", handed.refreshToken)
        assertTrue(handed.webOwned)
        assertEquals("g1", handed.generation)
        assertEquals("A1", handed.accessToken)
        assertEquals(base, handed.serverUrl)
        assertTrue("progress and leases bound to the pair carry on", sameSwitchSession(paired, handed))

        // An install from before the shell: no stored generation, the one derived from its refresh token is kept.
        val legacy = Session(base, "A1", "R1", sam)
        val migrated = handedOverSession(legacy, legacy)!!
        assertEquals(switchSessionGeneration(legacy), migrated.generation)
        assertTrue(sameSwitchSession(legacy, migrated))

        assertNull("the stored pair changed meanwhile", handedOverSession(paired, paired.copy(refreshToken = "R2")))
        assertNull("nothing to hand over", handedOverSession(paired.copy(refreshToken = null), paired.copy(refreshToken = null)))
    }

    @Test fun anAdoptedTokenKeepsThePersonsSessionAndANewPersonStartsAnother() {
        val owned = Session(base, "A1", null, sam, "g1", webOwned = true)
        val same = adoptedWebSession(owned, WebToken("token-A2-xyz", sam.copy(languages = listOf("en", "fr")))) { "fresh" }!!
        assertEquals("token-A2-xyz", same.accessToken)
        assertEquals("g1", same.generation)
        assertTrue(sameSwitchSession(owned, same))
        val other = adoptedWebSession(owned, WebToken("token-B1-xyz", Profile("p2", "Alex"))) { "fresh" }!!
        assertEquals("fresh", other.generation)
        assertFalse("nothing bound to Sam continues for Alex", sameSwitchSession(owned, other))
        assertNull("a TV that still holds its own pair takes nothing from the page", adoptedWebSession(owned.copy(webOwned = false, refreshToken = "R1"), WebToken("token-A2-xyz", sam)) { "x" })
        assertNull("no server", adoptedWebSession(owned.copy(serverUrl = null), WebToken("token-A2-xyz", sam)) { "x" })
    }

    // ---- The HTTP client asks the page and never refreshes itself -------------------------------

    private class Store(var session: Session) : SessionTokens {
        override suspend fun current(): Session = session
        override suspend fun saveRefreshedTokens(expected: Session, tokens: AuthTokens): Boolean = error("the TV must not refresh")
        override suspend fun clearExpiredSession(expected: Session): Boolean = error("the TV must not refresh")
    }

    @Test fun aRefusedTokenIsReplacedByTheWebAppsCurrentOne() = runTest {
        val store = Store(Session(base, "token-A1-xyz", null, sam, "g1", webOwned = true))
        val seen = mutableListOf<String?>()
        val json = headersOf(HttpHeaders.ContentType, ContentType.Application.Json.toString())
        val engine = MockEngine { request ->
            val bearer = request.headers[HttpHeaders.Authorization]
            seen += bearer
            assertFalse("no POST /auth/refresh from the TV", request.url.encodedPath.endsWith("/auth/refresh"))
            if (bearer == "Bearer token-A2-xyz") respond("""{"ok":true}""", HttpStatusCode.OK, json)
            else respond("""{"code":"token_expired","message":"expired"}""", HttpStatusCode.Unauthorized, json)
        }
        val rejectedSeen = mutableListOf<String?>()
        val page = object : AccessTokenSource {
            override suspend fun currentAccessToken(rejected: String?): String? {
                rejectedSeen += rejected
                // As WebSessionTokens does: the answer becomes the stored session.
                store.session = adoptedWebSession(store.session, WebToken("token-A2-xyz", sam)) { "x" }!!
                return "token-A2-xyz"
            }
        }
        val client = createStreamHubHttpClient(store, debug = false, onSessionExpired = { error("not ended") }, engine = engine, webTokens = page)
        try {
            assertEquals(HttpStatusCode.OK, client.get("$base/api/v1/auth/me").status)
            assertEquals(listOf("Bearer token-A1-xyz", "Bearer token-A2-xyz"), seen)
            assertEquals("the page is told which token was refused", listOf<String?>("token-A1-xyz"), rejectedSeen)
            // Not cached by the client: the next request reads the stored (adopted) token straight away.
            assertEquals(HttpStatusCode.OK, client.get("$base/api/v1/home").status)
            assertEquals("Bearer token-A2-xyz", seen.last())
            assertEquals(1, rejectedSeen.size)
        } finally { client.close() }
    }

    @Test fun aSignedOutWebAppLeavesTheRequestRefused() = runTest {
        val store = Store(Session(base, "token-A1-xyz", null, sam, "g1", webOwned = true))
        val json = headersOf(HttpHeaders.ContentType, ContentType.Application.Json.toString())
        val engine = MockEngine { respond("""{"code":"session_revoked","message":"gone"}""", HttpStatusCode.Unauthorized, json) }
        val page = object : AccessTokenSource { override suspend fun currentAccessToken(rejected: String?): String? = null }
        val client = createStreamHubHttpClient(store, debug = false, onSessionExpired = { error("the web app says so itself") }, engine = engine, webTokens = page)
        try {
            assertEquals(HttpStatusCode.Unauthorized, client.get("$base/api/v1/auth/me").status)
            assertNotNull(store.session.accessToken)
        } finally { client.close() }
    }
}
