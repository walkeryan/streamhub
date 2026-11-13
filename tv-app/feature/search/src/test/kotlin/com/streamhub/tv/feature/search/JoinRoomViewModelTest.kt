package com.streamhub.tv.feature.search

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.Room
import com.streamhub.tv.core.data.model.RoomTitle
import com.streamhub.tv.core.data.repo.RoomDirectory
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
class JoinRoomViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private fun refusal(status: Int, code: String) = ApiException(status, ApiError(code, "server text"), "server text")

    private class Fake : RoomDirectory {
        val log = mutableListOf<String>()
        var failLookup: Exception? = null
        var failJoin: Exception? = null
        /** When set, a lookup or a join waits on it. */
        var gate: CompletableDeferred<Unit>? = null
        var live = false
        override suspend fun create(titleId: String): Room = error("not used")
        override suspend fun lookup(code: String): Room {
            log += "lookup:$code"
            gate?.await()
            failLookup?.let { throw it }
            return Room(code = code, id = "tmdb:movie:603", live = live, hostName = "Alex", title = RoomTitle(id = "tmdb:movie:603", title = "The Long Film"))
        }
        override suspend fun close(code: String) = error("not used")
        override suspend fun shareUrl(room: Room): String? = null
        override suspend fun join(code: String, allowUnverifiedAudio: Boolean): String {
            log += "join:$code:$allowUnverifiedAudio"
            gate?.await()
            failJoin?.let { throw it }
            return "key-$code"
        }
        override fun openPlayer(key: String) { log += "open:$key" }
    }

    private fun JoinRoomViewModel.typeAll(text: String) = text.forEach(::type)

    // ---- the code ------------------------------------------------------------------------------

    @Test fun nothingIsLookedUpUntilAllSixCharactersAreIn() = runTest {
        val fake = Fake()
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("abc23")
        assertEquals("ABC23", vm.state.value.entry)
        assertTrue(fake.log.isEmpty())
        assertNull(vm.state.value.room)
        vm.type('4')
        assertEquals(listOf("lookup:ABC234"), fake.log)
        assertEquals("ABC234", vm.state.value.room?.code)
        assertEquals("The Long Film", vm.state.value.room?.title?.title)
        assertFalse(vm.state.value.looking)
    }

    @Test fun lookAlikesAreNotTypedAndASeventhCharacterIsIgnored() = runTest {
        val fake = Fake()
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("a0b1ciO")
        assertEquals("ABC", vm.state.value.entry)
        vm.typeAll("234")
        vm.type('Z')
        assertEquals("ABC234", vm.state.value.entry)
        assertEquals(1, fake.log.size)
    }

    @Test fun editingTheCodeTakesTheRoomAway() = runTest {
        val fake = Fake()
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("ABC234")
        vm.backspace()
        assertEquals("ABC23", vm.state.value.entry)
        assertNull(vm.state.value.room)
        assertNull(vm.state.value.error)
        vm.type('5')
        assertEquals(listOf("lookup:ABC234", "lookup:ABC235"), fake.log)
        vm.clear()
        assertEquals(JoinRoomState(), vm.state.value)
    }

    // ---- looking the room up -------------------------------------------------------------------

    @Test fun aWrongCodeIsFixedByTypingNotByTryingAgain() = runTest {
        val fake = Fake().apply { failLookup = refusal(404, "room_not_found") }
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("ABC234")
        val error = vm.state.value.error!!
        assertEquals("No room with that code", error.title)
        assertFalse(error.canRetry)
        assertNull(vm.state.value.room)
        assertFalse(vm.state.value.looking)
    }

    @Test fun aLookupThatFailedForAnotherReasonCanBeAskedAgain() = runTest {
        val fake = Fake().apply { failLookup = IOException("down") }
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("ABC234")
        assertEquals("Can't reach the server", vm.state.value.error?.title)
        assertTrue(vm.state.value.error!!.canRetry)
        fake.failLookup = null
        vm.lookUp()
        assertNull(vm.state.value.error)
        assertEquals("ABC234", vm.state.value.room?.code)
        assertEquals(listOf("lookup:ABC234", "lookup:ABC234"), fake.log)
    }

    @Test fun aRoomThisProfileMayNotWatchIsRefusedAtTheDoor() = runTest {
        val fake = Fake().apply { failLookup = refusal(403, "content_restricted") }
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("ABC234")
        assertEquals("Not available for this profile", vm.state.value.error?.title)
        assertNull(vm.state.value.room)
    }

    @Test fun anAnswerForACodeThatHasSinceBeenEditedIsDropped() = runTest {
        val fake = Fake().apply { gate = CompletableDeferred() }
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("ABC234")
        assertTrue(vm.state.value.looking)
        vm.backspace()
        fake.gate!!.complete(Unit)
        assertEquals("ABC23", vm.state.value.entry)
        assertNull(vm.state.value.room)
        assertFalse(vm.state.value.looking)
    }

    @Test fun lookUpDoesNothingOnAnIncompleteCode() = runTest {
        val fake = Fake()
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("ABC")
        vm.lookUp()
        vm.join()
        assertTrue(fake.log.isEmpty())
    }

    // ---- joining -------------------------------------------------------------------------------

    @Test fun joinTakesASeatAndOpensThePlayerOnIt() = runTest {
        val fake = Fake()
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("ABC234")
        vm.join()
        assertEquals(listOf("lookup:ABC234", "join:ABC234:false", "open:key-ABC234"), fake.log)
        assertFalse(vm.state.value.joining)
        assertNull(vm.state.value.error)
    }

    @Test fun aLiveRoomIsJoinedTheSameWay() = runTest {
        val fake = Fake().apply { live = true }
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("XYZ789")
        assertTrue(vm.state.value.room!!.live)
        vm.join()
        assertEquals("open:key-XYZ789", fake.log.last())
    }

    @Test fun unconfirmedAudioIsAskedAboutOnceThenSentAsConsent() = runTest {
        val fake = Fake().apply { failJoin = refusal(409, "audio_selection_required") }
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("ABC234")
        vm.join()
        assertTrue(vm.state.value.needsAudioConsent)
        assertNull(vm.state.value.error)
        assertEquals("ABC234", vm.state.value.room?.code)
        fake.failJoin = null
        vm.join()
        assertEquals(listOf("lookup:ABC234", "join:ABC234:false", "join:ABC234:true", "open:key-ABC234"), fake.log)
        // Still refused with consent given: now it is an error, not a second question.
        val stubborn = Fake().apply { failJoin = refusal(409, "audio_selection_required") }
        val second = JoinRoomViewModel(stubborn)
        second.typeAll("ABC234")
        second.join()
        second.join()
        assertEquals("Choose an audio source", second.state.value.error?.title)
        assertTrue(second.state.value.needsAudioConsent)
    }

    @Test fun aRefusedJoinKeepsTheRoomOnScreenWithItsReason() = runTest {
        for ((refused, title) in listOf(refusal(409, "room_full") to "The room is full", refusal(409, "user_stream_limit") to "Already watching", refusal(409, "room_unsupported") to "Can't be watched together")) {
            val fake = Fake().apply { failJoin = refused }
            val vm = JoinRoomViewModel(fake)
            vm.typeAll("ABC234")
            vm.join()
            assertEquals(title, vm.state.value.error?.title)
            assertEquals("ABC234", vm.state.value.room?.code)
            assertFalse(vm.state.value.joining)
            assertFalse(fake.log.any { it.startsWith("open") })
        }
    }

    @Test fun theCodeCannotBeEditedOrJoinedTwiceWhileAJoinIsOnItsWay() = runTest {
        val fake = Fake()
        val vm = JoinRoomViewModel(fake)
        vm.typeAll("ABC234")
        fake.gate = CompletableDeferred()
        vm.join()
        assertTrue(vm.state.value.joining)
        vm.join()
        vm.backspace()
        vm.clear()
        assertEquals("ABC234", vm.state.value.entry)
        assertEquals(1, fake.log.count { it.startsWith("join") })
        fake.gate!!.complete(Unit)
        assertEquals("open:key-ABC234", fake.log.last())
        assertFalse(vm.state.value.joining)
    }
}
