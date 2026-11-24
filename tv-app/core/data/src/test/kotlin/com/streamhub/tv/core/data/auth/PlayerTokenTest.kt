package com.streamhub.tv.core.data.auth

import com.streamhub.tv.core.data.model.Profile
import java.util.Base64
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The native player swaps the session's short access token for an hour-long player token and renews
 * it while it plays, because the page that owns the session may be hidden (and not answering) behind it.
 */
class PlayerTokenTest {
    private val sam = Profile("p1", "Sam", languages = listOf("en"))
    private val alex = Profile("p2", "Alex", languages = listOf("en"))
    private val base = "http://192.0.2.10:8787"
    private val now = 1_800_000_000L

    private fun jwt(exp: Long?): String {
        val enc = Base64.getUrlEncoder().withoutPadding()
        val body = if (exp == null) """{"sub":"p1"}""" else """{"sub":"p1","exp":$exp}"""
        return enc.encodeToString("""{"alg":"HS256"}""".toByteArray()) + "." + enc.encodeToString(body.toByteArray()) + ".sig"
    }

    private fun web(token: String?, profile: Profile = sam, generation: String = "g1") =
        Session(base, token, null, profile, generation, webOwned = true)

    @Test fun secondsLeftComeFromTheTokensOwnExpiry() {
        assertEquals(600L, tokenSecondsLeft(jwt(now + 600), now))
        assertEquals(-5L, tokenSecondsLeft(jwt(now - 5), now))
        assertNull("no exp", tokenSecondsLeft(jwt(null), now))
        assertNull(tokenSecondsLeft("not-a-jwt", now))
        assertNull(tokenSecondsLeft(null, now))
    }

    @Test fun aShortSessionTokenIsSwappedAndAnHourTokenOnlyNearItsEnd() {
        assertTrue("a 15-minute session token", playerTokenDue(web(jwt(now + 900)), now))
        assertTrue("expired", playerTokenDue(web(jwt(now - 1)), now))
        assertTrue("unreadable", playerTokenDue(web("opaque"), now))
        assertFalse("an hour token with 45 minutes left", playerTokenDue(web(jwt(now + 45 * 60)), now))
        assertTrue("an hour token with 15 minutes left", playerTokenDue(web(jwt(now + 15 * 60)), now))
    }

    @Test fun aSessionTheTvRefreshesItselfIsLeftAlone() {
        val own = Session(base, jwt(now + 60), "refresh", sam, "g1", webOwned = false)
        assertFalse(playerTokenDue(own, now))
        assertNull(sessionWithPlayerToken(own, own, jwt(now + 3600)))
    }

    @Test fun onlyTheSamePersonAndHandOverTakeTheToken() {
        val owner = web(jwt(now + 60))
        val token = jwt(now + 3600)
        assertEquals(token, sessionWithPlayerToken(owner, owner, token)?.accessToken)
        assertEquals("g1", sessionWithPlayerToken(owner, owner, token)?.generation)
        assertNull("someone else now", sessionWithPlayerToken(owner, web(jwt(now + 60), alex, "g2"), token))
        assertNull("handed over since", sessionWithPlayerToken(owner, web(jwt(now + 60), sam, "g2"), token))
        assertNull("junk", sessionWithPlayerToken(owner, owner, "has space"))
    }
}
