package com.streamhub.tv.feature.onboarding

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.Profile
import com.streamhub.tv.core.data.model.SwitchableUser
import com.streamhub.tv.core.data.repo.SessionEnd
import com.streamhub.tv.core.data.repo.SwitchResult
import com.streamhub.tv.core.data.repo.UserSwitcher
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class WhoIsWatchingViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private val alex = SwitchableUser(id = "u1", displayName = "Alex", roleName = "Owner", hasPin = true, current = true)
    private val sam = SwitchableUser(id = "u2", displayName = "Sam", roleName = "Family", hasPin = true)
    private val kit = SwitchableUser(id = "u3", displayName = "Kit", roleName = "Kid")
    private fun ok(user: SwitchableUser, theme: String = "midnight") = SwitchResult.Ok(Profile(id = "p-${user.id}", name = user.displayName, themeId = theme))

    /** The picker's side of UserSwitchRepository: a list (or its absence, or a failure) and scripted answers to a switch. */
    private class Fake(var users: List<SwitchableUser>? = emptyList()) : UserSwitcher {
        var failList: Exception? = null
        var answer: (userId: String, pin: String?) -> SwitchResult = { _, _ -> error("no switch expected") }
        /** When set, a switch waits on it: the screen is busy meanwhile. */
        var gate: CompletableDeferred<Unit>? = null
        val calls = mutableListOf<String>()
        var lists = 0
        override suspend fun switchable(): List<SwitchableUser>? { lists++; failList?.let { throw it }; return users }
        override suspend fun switchTo(userId: String, pin: String?): SwitchResult {
            calls += "$userId:${pin ?: "-"}"
            gate?.await()
            return answer(userId, pin)
        }
    }

    private var now = 100_000L
    private val themes = mutableListOf<String>()
    private fun vm(fake: Fake) = WhoIsWatchingViewModel(fake, serverUrl = { "http://hub.test:8787" }, applyTheme = { themes += it }, nowMs = { now })

    // ---- the list ------------------------------------------------------------------------------

    @Test fun thePeopleWhoCanBePickedAreListed() = runTest {
        val s = vm(Fake(listOf(alex, sam, kit))).state.value
        assertFalse(s.loading)
        assertEquals(listOf("u1", "u2", "u3"), s.users.map { it.id })
        assertEquals("http://hub.test:8787", s.serverUrl)
        assertNull(s.outcome)
        assertNull(s.loadError)
    }

    @Test fun aPersonalDeviceOrAServerWithoutThePickerHasNothingToAsk() = runTest {
        // null: 403 not_shared_device, or the route is missing on an old server.
        assertEquals(WhoOutcome.Absent, vm(Fake(users = null)).state.value.outcome)
        // Nobody is set up to be picked on shared TVs.
        assertEquals(WhoOutcome.Absent, vm(Fake(users = emptyList())).state.value.outcome)
    }

    @Test fun aListThatCannotBeReadSaysWhyAndCanBeReadAgain() = runTest {
        val fake = Fake(listOf(alex, sam)).apply { failList = IOException("Connection refused") }
        val vm = vm(fake)
        assertEquals("Can't reach the server", vm.state.value.loadError?.title)
        assertFalse(vm.state.value.loading)
        assertFalse(vm.state.value.canPairAgain)
        assertNull(vm.state.value.outcome)
        fake.failList = null
        vm.load()
        assertNull(vm.state.value.loadError)
        assertEquals(2, vm.state.value.users.size)
    }

    @Test fun aSuspendedAccountOffersPairingAgainBesideRetry() = runTest {
        val fake = Fake(listOf(alex)).apply { failList = ApiException(401, ApiError("user_suspended", "x"), "x") }
        val s = vm(fake).state.value
        assertEquals("Account suspended", s.loadError?.title)
        assertTrue(s.canPairAgain)
        // Away from home is said too, but pairing again would not change it.
        val away = vm(Fake(listOf(alex)).apply { failList = ApiException(403, ApiError("remote_access_denied", "x"), "x") }).state.value
        assertEquals("Home network only", away.loadError?.title)
        assertFalse(away.canPairAgain)
    }

    // ---- picking -------------------------------------------------------------------------------

    @Test fun someoneWithoutAPinIsSwitchedToAtOnce() = runTest {
        val fake = Fake(listOf(alex, kit)).apply { answer = { _, _ -> ok(kit, theme = "ocean") } }
        val vm = vm(fake)
        vm.pick(kit, keepCurrent = false)
        assertEquals(listOf("u3:-"), fake.calls)
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
        assertEquals(listOf("ocean"), themes)
        assertNull(vm.state.value.pinFor)
    }

    @Test fun someoneWithAPinIsAskedForItBeforeAnythingIsSent() = runTest {
        val fake = Fake(listOf(alex, sam)).apply { answer = { _, pin -> if (pin == "2468") ok(sam) else SwitchResult.PinInvalid } }
        val vm = vm(fake)
        vm.pick(sam, keepCurrent = false)
        assertEquals(sam, vm.state.value.pinFor)
        assertTrue(fake.calls.isEmpty())
        vm.submitPin("2468")
        assertEquals(listOf("u2:2468"), fake.calls)
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
        assertNull(vm.state.value.pinFor)
    }

    @Test fun aWrongPinKeepsThePadOpenAndEmptiesIt() = runTest {
        val fake = Fake(listOf(alex, sam)).apply { answer = { _, _ -> SwitchResult.PinInvalid } }
        val vm = vm(fake)
        vm.pick(sam, keepCurrent = false)
        val attempt = vm.state.value.pinAttempt
        vm.submitPin("0000")
        val s = vm.state.value
        assertEquals(sam, s.pinFor)
        assertEquals("That PIN isn't right. Try again.", s.pinError)
        assertEquals(attempt + 1, s.pinAttempt)
        assertFalse(s.busy)
        assertNull(s.outcome)
        // Back closes the pad without switching.
        vm.cancelPin()
        assertNull(vm.state.value.pinFor)
        assertNull(vm.state.value.pinError)
    }

    @Test fun aPinTheListDidNotMentionIsStillAskedFor() = runTest {
        // The list said "no PIN"; the server says otherwise (one was set a moment ago).
        val fake = Fake(listOf(alex, kit)).apply { answer = { _, pin -> if (pin == null) SwitchResult.PinRequired else ok(kit) } }
        val vm = vm(fake)
        vm.pick(kit, keepCurrent = false)
        assertEquals(kit, vm.state.value.pinFor)
        assertNull(vm.state.value.pinError)
        vm.submitPin("1357")
        assertEquals(listOf("u3:-", "u3:1357"), fake.calls)
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
    }

    @Test fun someoneWhoMustSetAPinFirstIsToldWhere() = runTest {
        val fake = Fake(listOf(alex, kit)).apply { answer = { _, _ -> SwitchResult.PinSetupRequired } }
        val vm = vm(fake)
        vm.pick(kit, keepCurrent = false)
        assertEquals("Kit needs a PIN first", vm.state.value.notice?.title)
        assertNull(vm.state.value.pinFor)
        assertNull(vm.state.value.outcome)
    }

    // ---- the person already signed in ----------------------------------------------------------

    @Test fun onTheColdStartPickerTheCurrentPersonProvesItWithTheirPin() = runTest {
        val fake = Fake(listOf(alex, sam)).apply { answer = { _, pin -> if (pin == "1111") ok(alex) else SwitchResult.PinInvalid } }
        val vm = vm(fake)
        vm.pick(alex, keepCurrent = false)
        // Not let through on the strength of being signed in: the server checks the PIN of a switch to oneself.
        assertEquals(alex, vm.state.value.pinFor)
        assertNull(vm.state.value.outcome)
        vm.submitPin("9999")
        assertEquals("That PIN isn't right. Try again.", vm.state.value.pinError)
        vm.submitPin("1111")
        assertEquals(listOf("u1:9999", "u1:1111"), fake.calls)
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
    }

    @Test fun theCurrentPersonWithoutAPinHasNothingToProve() = runTest {
        val fake = Fake(listOf(alex.copy(hasPin = false), sam))
        val vm = vm(fake)
        vm.pick(fake.users!!.first(), keepCurrent = false)
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
        assertTrue(fake.calls.isEmpty())
    }

    @Test fun openedFromTheRailTheCurrentPersonJustClosesThePicker() = runTest {
        val fake = Fake(listOf(alex, sam))
        val vm = vm(fake)
        vm.pick(alex, keepCurrent = true)
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
        assertTrue(fake.calls.isEmpty())
        assertNull(vm.state.value.pinFor)
    }

    // ---- refusals ------------------------------------------------------------------------------

    @Test fun tooManyWrongPinsLockThePadForAsLongAsTheServerSays() = runTest {
        val fake = Fake(listOf(alex, sam)).apply { answer = { _, _ -> SwitchResult.LockedOut(retryAfterSeconds = 45) } }
        val vm = vm(fake)
        vm.pick(sam, keepCurrent = false)
        vm.submitPin("0000")
        assertEquals(now + 45_000, vm.state.value.lockedUntilMs)
        assertEquals(sam, vm.state.value.pinFor)
        // Nothing is sent while locked out.
        vm.submitPin("2468")
        assertEquals(1, fake.calls.size)
        // The countdown reaching zero early (a clock that ran fast) does not unlock it.
        now += 44_000
        vm.lockoutOver()
        assertEquals(100_000L + 45_000, vm.state.value.lockedUntilMs)
        now += 1_000
        vm.lockoutOver()
        assertEquals(0L, vm.state.value.lockedUntilMs)
        fake.answer = { _, _ -> ok(sam) }
        vm.submitPin("2468")
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
    }

    @Test fun someoneNoLongerListedIsSaidAndTheListIsReadAgain() = runTest {
        val fake = Fake(listOf(alex, sam, kit)).apply { answer = { _, _ -> SwitchResult.NotListed } }
        val vm = vm(fake)
        assertEquals(1, fake.lists)
        // The server has taken Kit off shared TVs since the list was read.
        fake.users = listOf(alex, sam)
        vm.pick(kit, keepCurrent = false)
        assertEquals("No longer here", vm.state.value.notice?.title)
        assertEquals(2, fake.lists)
        assertEquals(listOf("u1", "u2"), vm.state.value.users.map { it.id })
        assertNull(vm.state.value.outcome)
    }

    @Test fun aSuspendedAccountIsExplainedHereAndASignedOutTvIsLeftToTheShell() = runTest {
        val suspended = Fake(listOf(alex, sam)).apply { answer = { _, _ -> SwitchResult.SessionEnded(SessionEnd.Suspended) } }
        val vm = vm(suspended)
        vm.pick(sam, keepCurrent = false)
        vm.submitPin("2468")
        assertEquals("Account suspended", vm.state.value.notice?.title)
        assertNull(vm.state.value.pinFor)
        assertFalse(vm.state.value.busy)
        // Signed out: the repository has dropped the tokens and told the shell, which explains and goes
        // to pairing. The picker says nothing of its own and is not left busy.
        val revoked = Fake(listOf(alex, sam)).apply { answer = { _, _ -> SwitchResult.SessionEnded(SessionEnd.Revoked) } }
        val second = vm(revoked)
        second.pick(sam, keepCurrent = false)
        second.submitPin("2468")
        assertNull(second.state.value.notice)
        assertNull(second.state.value.pinFor)
        assertFalse(second.state.value.busy)
        assertNull(second.state.value.outcome)
    }

    @Test fun aFailureWithThePadOpenIsShownInThePadSoThePinCanBeSentAgain() = runTest {
        val offline = SwitchResult.Failed(UserError("Can't reach the server", "Check the network.", canRetry = true))
        val fake = Fake(listOf(alex, sam)).apply { answer = { _, _ -> offline } }
        val vm = vm(fake)
        vm.pick(sam, keepCurrent = false)
        vm.submitPin("2468")
        assertEquals("Can't reach the server. Check the network.", vm.state.value.pinError)
        assertEquals(sam, vm.state.value.pinFor)
        assertNull(vm.state.value.notice)
    }

    @Test fun aFailureWithoutAPadIsANoticeAndTryAgainTriesTheSamePerson() = runTest {
        val offline = SwitchResult.Failed(UserError("Can't reach the server", "Check the network.", canRetry = true))
        val fake = Fake(listOf(alex, kit)).apply { answer = { _, _ -> offline } }
        val vm = vm(fake)
        vm.pick(kit, keepCurrent = false)
        assertEquals(offline.error, vm.state.value.notice)
        fake.answer = { _, _ -> ok(kit) }
        vm.retryNotice()
        assertEquals(listOf("u3:-", "u3:-"), fake.calls)
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
        assertNull(vm.state.value.notice)
    }

    @Test fun acknowledgingANoticeReadsTheListAgainAndFindsOutThereIsNoPicker() = runTest {
        val personal = SwitchResult.Failed(UserError("Not a shared TV", "This TV is one person's own device.", canRetry = false))
        val fake = Fake(listOf(alex, kit)).apply { answer = { _, _ -> personal } }
        val vm = vm(fake)
        vm.pick(kit, keepCurrent = false)
        assertEquals("Not a shared TV", vm.state.value.notice?.title)
        // 403 not_shared_device on the list as well: there is no picker after all.
        fake.users = null
        vm.dismissNotice()
        assertNull(vm.state.value.notice)
        assertEquals(WhoOutcome.Absent, vm.state.value.outcome)
    }

    @Test fun nothingElseIsStartedWhileASwitchIsOnItsWay() = runTest {
        val fake = Fake(listOf(alex, sam, kit)).apply { gate = CompletableDeferred(); answer = { _, _ -> ok(kit) } }
        val vm = vm(fake)
        vm.pick(kit, keepCurrent = false)
        assertTrue(vm.state.value.busy)
        vm.pick(sam, keepCurrent = false)
        vm.retryNotice()
        assertNull(vm.state.value.pinFor)
        assertEquals(listOf("u3:-"), fake.calls)
        fake.gate!!.complete(Unit)
        assertFalse(vm.state.value.busy)
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
    }

    @Test fun aThemeThatCannotBeAppliedDoesNotUndoTheSwitch() = runTest {
        val fake = Fake(listOf(alex, kit)).apply { answer = { _, _ -> ok(kit) } }
        val vm = WhoIsWatchingViewModel(fake, serverUrl = { null }, applyTheme = { throw IOException("disk full") }, nowMs = { now })
        vm.pick(kit, keepCurrent = false)
        assertEquals(WhoOutcome.Done, vm.state.value.outcome)
    }

    // ---- faces ---------------------------------------------------------------------------------

    @Test fun aPictureIsAnAbsoluteAddressOrAPathOnThePairedServer() {
        assertEquals("https://img.test/a.png", personImage(alex.copy(picture = "https://img.test/a.png"), "hub.test"))
        assertEquals("http://hub.test:8787/api/v1/images/t", personImage(alex.copy(avatar = "/api/v1/images/t"), "hub.test"))
        assertNull(personImage(alex.copy(avatar = "🦊"), "hub.test"))
        assertNull(personImage(alex.copy(avatar = "//elsewhere.test/x.png"), "hub.test"))
        assertNull(personImage(alex.copy(avatar = "/api/v1/images/t"), null))
        assertEquals("🦊", personGlyph(alex.copy(avatar = "🦊")))
        assertEquals("A", personGlyph(alex))
        assertEquals("?", personGlyph(alex.copy(displayName = " ")))
    }
}
