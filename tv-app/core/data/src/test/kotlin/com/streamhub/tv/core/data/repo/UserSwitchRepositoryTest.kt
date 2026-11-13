package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.Profile
import com.streamhub.tv.core.data.model.SwitchableUser
import com.streamhub.tv.core.data.model.SwitchableUsers
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException

class UserSwitchRepositoryTest {
    private val tokens = AuthTokens("access-new", "refresh-new", Profile(id = "p2", name = "Sam"))
    private fun refusal(status: Int, code: String, retryAfter: Int? = null) = ApiException(status, ApiError(code, "server text"), "server text", retryAfter)

    private class Fake(
        var users: () -> List<SwitchableUser> = { emptyList() },
        var post: (String, String?) -> AuthTokens,
    ) : SwitchBackend {
        val log = mutableListOf<String>()
        var failAccess = false
        override suspend fun switchable() = users()
        override suspend fun post(userId: String, pin: String?): AuthTokens { log += "post:$userId:${pin ?: "-"}"; return post.invoke(userId, pin) }
        override suspend fun refreshSession() { log += "refresh" }
        override suspend fun release() { log += "release" }
        override suspend fun save(tokens: AuthTokens) { log += "save:${tokens.profile.id}" }
        override suspend fun saveProfile(original: Session, targetProfileId: String, tokens: AuthTokens) { log += "saveProfile:$targetProfileId" }
        override suspend fun reloadAccess() { log += "access"; if (failAccess) throw IOException("down") }
        override suspend fun sessionEnded(end: SessionEnd) { log += "ended:$end" }
    }

    @Test fun successSavesTokensClearsCachesAndReadsTheNewAccess() = runTest {
        val fake = Fake { _, _ -> tokens }
        val result = UserSwitchRepository(fake).switchTo("u2", "1234")
        assertEquals(SwitchResult.Ok(tokens.profile), result)
        // Caches of the person leaving go before the new tokens land; access is read last.
        assertEquals(listOf("post:u2:1234", "release", "save:p2", "access"), fake.log)
    }

    @Test fun aFailedAccessReadDoesNotUndoTheSwitch() = runTest {
        val fake = Fake { _, _ -> tokens }.apply { failAccess = true }
        assertEquals(SwitchResult.Ok(tokens.profile), UserSwitchRepository(fake).switchTo("u2", null))
    }

    @Test fun pinRefusalsMapToTheirOwnResults() = runTest {
        suspend fun result(e: ApiException) = UserSwitchRepository(Fake { _, _ -> throw e }).switchTo("u2", null)
        assertEquals(SwitchResult.PinRequired, result(refusal(401, "pin_required")))
        assertEquals(SwitchResult.PinInvalid, result(refusal(401, "pin_invalid")))
        assertEquals(SwitchResult.PinSetupRequired, result(refusal(409, "pin_setup_required")))
    }

    @Test fun aRefusedSwitchSavesAndClearsNothing() = runTest {
        val fake = Fake { _, _ -> throw refusal(401, "pin_invalid") }
        UserSwitchRepository(fake).switchTo("u2", "0000")
        // One attempt only: a wrong PIN is never sent twice, and no token refresh is tried for it.
        assertEquals(listOf("post:u2:0000"), fake.log)
    }

    @Test fun lockoutCarriesRetryAfter() = runTest {
        suspend fun result(retryAfter: Int?) = UserSwitchRepository(Fake { _, _ -> throw refusal(429, "rate_limited", retryAfter) }).switchTo("u2", "1234")
        assertEquals(SwitchResult.LockedOut(45), result(45))
        assertEquals(SwitchResult.LockedOut(60), result(null))
        assertEquals(SwitchResult.LockedOut(1), result(0))
        assertEquals(SwitchResult.LockedOut(3600), result(99_999))
    }

    @Test fun otherFailuresAreWordedForTheViewer() = runTest {
        suspend fun result(e: Exception) = UserSwitchRepository(Fake { _, _ -> throw e }).switchTo("u2", null)
        val personal = result(refusal(403, "not_shared_device")) as SwitchResult.Failed
        assertEquals("Not a shared TV", personal.error.title)
        val offline = result(IOException("Connection refused")) as SwitchResult.Failed
        assertEquals("Can't reach the server", offline.error.title)
        assertTrue(offline.error.canRetry)
        val server = result(ApiException(503, null, "HTTP 503")) as SwitchResult.Failed
        assertEquals("Server problem", server.error.title)
    }

    @Test fun aSessionThatNoLongerStandsIsNotAPinAnswer() = runTest {
        // Signed out elsewhere, or the session changed hands: the tokens go and the shell is told.
        val revoked = Fake { _, _ -> throw refusal(401, "session_revoked") }
        assertEquals(SwitchResult.SessionEnded(SessionEnd.Revoked), UserSwitchRepository(revoked).switchTo("u2", "1234"))
        assertEquals(listOf("post:u2:1234", "ended:Revoked"), revoked.log)
        // Suspended: about the person signed in here, so nobody can be picked; nothing is saved or released.
        val suspended = Fake { _, _ -> throw refusal(401, "user_suspended") }
        assertEquals(SwitchResult.SessionEnded(SessionEnd.Suspended), UserSwitchRepository(suspended).switchTo("u2", null))
        assertEquals(listOf("post:u2:-", "ended:Suspended"), suspended.log)
    }

    @Test fun aSwitchConflictIsRepeatedOnce() = runTest {
        var calls = 0
        val fake = Fake { _, _ -> if (++calls == 1) throw refusal(409, "switch_conflict") else tokens }
        assertEquals(SwitchResult.Ok(tokens.profile), UserSwitchRepository(fake).switchTo("u2", "1234"))
        assertEquals(listOf("post:u2:1234", "post:u2:1234", "release", "save:p2", "access"), fake.log)
        // Still in conflict: said, with Try again, and not repeated a third time.
        val stuck = Fake { _, _ -> throw refusal(409, "switch_conflict") }
        val result = UserSwitchRepository(stuck).switchTo("u2", null) as SwitchResult.Failed
        assertEquals("Couldn't switch just now", result.error.title)
        assertTrue(result.error.canRetry)
        assertEquals(listOf("post:u2:-", "post:u2:-"), stuck.log)
    }

    @Test fun aStaleSessionAndAConflictEachGetTheirOwnRepeat() = runTest {
        var calls = 0
        val fake = Fake { _, _ ->
            when (++calls) { 1 -> throw refusal(401, "session_stale"); 2 -> throw refusal(409, "switch_conflict"); else -> tokens }
        }
        assertEquals(SwitchResult.Ok(tokens.profile), UserSwitchRepository(fake).switchTo("u2", null))
        assertEquals(listOf("post:u2:-", "refresh", "post:u2:-", "post:u2:-", "release", "save:p2", "access"), fake.log)
    }

    @Test fun aRefreshThatFindsTheSessionRevokedEndsIt() = runTest {
        val fake = object : SwitchBackend by Fake({ emptyList() }, { _, _ -> throw refusal(401, "token_expired") }) {
            val ended = mutableListOf<SessionEnd>()
            override suspend fun refreshSession() { throw refusal(401, "session_revoked") }
            override suspend fun sessionEnded(end: SessionEnd) { ended += end }
        }
        assertEquals(SwitchResult.SessionEnded(SessionEnd.Revoked), UserSwitchRepository(fake).switchTo("u2", "1234"))
        assertEquals(listOf(SessionEnd.Revoked), fake.ended)
    }

    @Test fun aStaleSessionIsRefreshedAndTheSwitchRepeatedOnce() = runTest {
        for (code in listOf("session_stale", "token_expired")) {
            var calls = 0
            val fake = Fake { _, _ -> if (++calls == 1) throw refusal(401, code) else tokens }
            assertEquals(SwitchResult.Ok(tokens.profile), UserSwitchRepository(fake).switchTo("u2", "1234"))
            assertEquals(listOf("post:u2:1234", "refresh", "post:u2:1234", "release", "save:p2", "access"), fake.log)
        }
        // Still stale after the one retry: reported, not retried again.
        val stuck = Fake { _, _ -> throw refusal(401, "session_stale") }
        val result = UserSwitchRepository(stuck).switchTo("u2", null) as SwitchResult.Failed
        assertEquals("Access updated", result.error.title)
        assertEquals(2, stuck.log.count { it.startsWith("post") })
    }

    @Test fun pickerIsAbsentOnOldServersAndPersonalDevices() = runTest {
        // The framework's answer for an unknown path carries no code of the server's own.
        for (unknownRoute in listOf(ApiException(404, null, "HTTP 404"), refusal(404, "http_error"), refusal(404, ""))) {
            assertNull(UserSwitchRepository(Fake(users = { throw unknownRoute }) { _, _ -> tokens }).switchable())
        }
        assertNull(UserSwitchRepository(Fake(users = { throw refusal(403, "not_shared_device") }) { _, _ -> tokens }).switchable())
        val users = listOf(SwitchableUser(id = "u1", displayName = "Alex", current = true), SwitchableUser(id = "u2", displayName = "Sam", hasPin = true))
        assertEquals(users, UserSwitchRepository(Fake(users = { users }) { _, _ -> tokens }).switchable())
    }

    @Test fun otherPickerFailuresAreNotMistakenForNoPicker() = runTest {
        val failures = listOf(
            ApiException(503, null, "HTTP 503"), IOException("down"),
            // Refusals about the account or the network it is on are said, not hidden behind "no picker".
            refusal(403, "remote_access_denied"), refusal(403, "forbidden"), refusal(401, "user_suspended"), refusal(404, "not_found"),
        )
        for (failure in failures) {
            try { UserSwitchRepository(Fake(users = { throw failure }) { _, _ -> tokens }).switchable(); fail("must be thrown") }
            catch (e: Exception) { assertEquals(failure, e) }
        }
    }

    @Test fun aPersonWhoIsGoneIsReportedAndThePickerStays() = runTest {
        val users = listOf(SwitchableUser(id = "u1", displayName = "Alex", current = true))
        for (gone in listOf(refusal(404, "not_found"), ApiException(404, null, "HTTP 404"))) {
            val fake = Fake(users = { users }) { _, _ -> throw gone }
            val repo = UserSwitchRepository(fake)
            assertEquals(SwitchResult.NotListed, repo.switchTo("u9", null))
            // One attempt, nothing saved; the list is still there to be read again.
            assertEquals(listOf("post:u9:-"), fake.log)
            assertEquals(users, repo.switchable())
        }
    }

    @Test fun legacyProfileSwitchCommitsThroughTheSamePath() = runTest {
        val fake = Fake { _, _ -> tokens }
        UserSwitchRepository(fake).commitProfileSwitch(Session("http://hub.test:8787", "a", "r", Profile(id = "p1", name = "Alex")), "p2", tokens)
        assertEquals(listOf("release", "saveProfile:p2", "access"), fake.log)
    }

    @Test fun switchableUsersDecodeFromTheWire() {
        val list = StreamHubJson.decodeFromString(SwitchableUsers.serializer(), """
            {"users":[{"id":"u1","display_name":"Alex","avatar":null,"picture":"https://img.test/a.png","role_name":"Owner","has_pin":true,"current":true,"extra":1},
                      {"id":"u2","display_name":"Sam"}]}
        """.trimIndent())
        assertEquals(2, list.users.size)
        assertTrue(list.users[0].hasPin && list.users[0].current)
        assertEquals("Owner", list.users[0].roleName)
        assertFalse(list.users[1].hasPin || list.users[1].current)
        assertEquals(emptyList<SwitchableUser>(), StreamHubJson.decodeFromString(SwitchableUsers.serializer(), "{}").users)
    }
}
