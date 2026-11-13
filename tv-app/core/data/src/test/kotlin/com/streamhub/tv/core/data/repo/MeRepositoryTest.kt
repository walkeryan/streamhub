package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.Me
import com.streamhub.tv.core.data.model.Permissions
import com.streamhub.tv.core.data.model.Profile
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class MeRepositoryTest {
    private val savedTokens = mutableListOf<AuthTokens>()
    private val sharedFlags = mutableListOf<Boolean>()
    private fun me(vararg permissions: String, shared: Boolean = false, status: String = "active", tokens: AuthTokens? = null) =
        Me(status = status, permissions = permissions.toList(), sharedDevice = shared, tokens = tokens)

    private fun TestScope.repo(admin: Boolean = false, signals: MutableSharedFlow<Unit>? = null, fetch: suspend () -> Me) = MeRepository(
        fetch = fetch,
        isAdminProfile = { admin },
        saveTokens = { savedTokens += it },
        rememberSharedDevice = { sharedFlags += it },
        refreshSignals = signals ?: emptyFlow(),
        scope = backgroundScope,
    )

    @Test fun nothingIsOfferedBeforeTheFirstAnswer() = runTest {
        val repo = repo { me(Permissions.ROOMS_JOIN) }
        assertEquals(MeState.Unknown, repo.state.value)
        assertFalse(repo.can(Permissions.ROOMS_JOIN))
    }

    @Test fun canFollowsTheServersPermissionList() = runTest {
        val repo = repo { me(Permissions.ROOMS_JOIN, Permissions.RECORDINGS_VIEW, shared = true) }
        repo.refresh()
        assertTrue(repo.can(Permissions.ROOMS_JOIN))
        assertTrue(repo.can(Permissions.RECORDINGS_VIEW))
        assertFalse(repo.can(Permissions.ROOMS_CREATE))
        assertFalse(repo.can(Permissions.RECORDINGS_SCHEDULE))
        assertTrue(repo.state.value.sharedDevice)
        assertEquals(listOf(true), sharedFlags)
    }

    @Test fun aUserWhoIsNotActiveHoldsNothing() = runTest {
        val repo = repo { me(Permissions.ROOMS_JOIN, status = "suspended") }
        repo.refresh()
        assertFalse(repo.can(Permissions.ROOMS_JOIN))
    }

    @Test fun serverWithoutTheEndpointOffersEverythingButAdminScreens() = runTest {
        val repo = repo { throw ApiException(404, null, "HTTP 404") }
        assertEquals(MeState.Absent(admin = false), repo.refresh())
        for (p in listOf(Permissions.ROOMS_CREATE, Permissions.ROOMS_JOIN, Permissions.RECORDINGS_SCHEDULE, Permissions.RECORDINGS_VIEW, Permissions.REQUESTS_CREATE)) {
            assertTrue(p, repo.can(p))
        }
        for (p in Permissions.ADMIN_ONLY) assertFalse(p, repo.can(p))
        assertFalse(repo.state.value.sharedDevice)
        assertEquals(listOf(false), sharedFlags)
    }

    @Test fun serverWithoutTheEndpointKeepsAdminScreensForAnAdminProfile() = runTest {
        val repo = repo(admin = true) { throw ApiException(404, null, "HTTP 404") }
        repo.refresh()
        for (p in Permissions.ADMIN_ONLY) assertTrue(p, repo.can(p))
    }

    @Test fun aFailedReadKeepsWhatWasKnown() = runTest {
        var fail: Exception? = null
        val repo = repo { fail?.let { throw it }; me(Permissions.ROOMS_JOIN) }
        val known = repo.refresh()
        for (failure in listOf(IOException("down"), ApiException(503, null, "HTTP 503"), ApiException(401, null, "HTTP 401"))) {
            fail = failure
            assertEquals(known, repo.refresh())
            assertTrue(repo.can(Permissions.ROOMS_JOIN))
        }
        // And before anything was known, a failure leaves it unknown rather than guessing "absent".
        val cold = repo { throw IOException("down") }
        assertEquals(MeState.Unknown, cold.refresh())
    }

    @Test fun tokensOfAnApprovedUserAreSavedOnceAndNotKept() = runTest {
        val tokens = AuthTokens("access-1", "refresh-1", Profile(id = "p1", name = "Sam"))
        val repo = repo { me(Permissions.ROOMS_JOIN, tokens = tokens) }
        val state = repo.refresh() as MeState.Known
        assertEquals(listOf(tokens), savedTokens)
        assertNull(state.me.tokens)
        assertFalse(me(tokens = tokens).toString().contains("access-1"))
    }

    @Test fun anAnswerForThePreviousUserIsDropped() = runTest {
        val gate = CompletableDeferred<Me>()
        val repo = repo { gate.await() }
        val pending = async { repo.refresh() }
        runCurrent()
        repo.clear()
        gate.complete(me(Permissions.ROOMS_CREATE))
        assertEquals(MeState.Unknown, pending.await())
        assertFalse(repo.can(Permissions.ROOMS_CREATE))
        assertTrue(sharedFlags.isEmpty())
    }

    @Test fun aTokenRefreshRereadsAccessButNotInALoop() = runTest {
        val signals = MutableSharedFlow<Unit>(extraBufferCapacity = 8)
        var reads = 0
        val repo = repo(signals = signals) { reads++; me(if (reads == 1) Permissions.ROOMS_JOIN else Permissions.ROOMS_CREATE) }
        runCurrent()
        signals.emit(Unit)
        runCurrent()
        assertEquals(1, reads)
        assertTrue(repo.can(Permissions.ROOMS_JOIN))
        // A burst while the first is being answered collapses into one more read, after a pause.
        repeat(5) { signals.emit(Unit) }
        runCurrent()
        assertEquals(1, reads)
        advanceTimeBy(5_001)
        runCurrent()
        assertEquals(2, reads)
        assertTrue(repo.can(Permissions.ROOMS_CREATE))
    }

    @Test fun meDecodesFromTheWireAndToleratesGaps() {
        val full = StreamHubJson.decodeFromString(Me.serializer(), """
            {"status":"active","user":{"id":"u1","kind":"managed","display_name":"Sam","avatar":null,"picture":null,"role_name":"Member","has_pin":true,"created_at":"2026-01-01T00:00:00Z"},
             "role":{"id":"r1","name":"Member","builtin":true},"permissions":["watch.live","rooms.join","something.new"],
             "limits":{"max_streams":2,"rating_ceiling_movie":null,"rating_ceiling_tv":null,"allow_unrated":true},
             "shared_device":true,"adult_unlocked":false,"tokens":null,"added_later":1}
        """.trimIndent())
        assertEquals("Sam", full.user.displayName)
        assertTrue(full.user.hasPin)
        assertTrue(full.sharedDevice)
        assertEquals(listOf("watch.live", "rooms.join", "something.new"), full.permissions)
        val bare = StreamHubJson.decodeFromString(Me.serializer(), """{"user":{"id":"u2"},"role":null}""")
        assertEquals("active", bare.status)
        assertEquals(emptyList<String>(), bare.permissions)
        assertFalse(bare.sharedDevice)
    }
}
