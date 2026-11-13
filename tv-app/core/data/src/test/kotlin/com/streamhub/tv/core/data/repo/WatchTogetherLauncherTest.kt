package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.Room
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class WatchTogetherLauncherTest {
    private class Fake : RoomDirectory {
        val log = mutableListOf<String>()
        var failCreate: Exception? = null
        var failJoin: Exception? = null
        /** When set, create() waits on it: the dialog can be cancelled while the room is being made. */
        var gate: CompletableDeferred<Unit>? = null
        override suspend fun create(titleId: String): Room {
            log += "create:$titleId"
            gate?.await()
            failCreate?.let { throw it }
            return Room(code = "ABC234", id = titleId, live = titleId.startsWith("ch:"), isHost = true)
        }
        override suspend fun lookup(code: String): Room = error("not used")
        override suspend fun close(code: String) { log += "close:$code" }
        override suspend fun shareUrl(room: Room): String? = "http://hub.test:8787/room/${room.code}"
        override suspend fun join(code: String, allowUnverifiedAudio: Boolean): String {
            log += "join:$code:$allowUnverifiedAudio"
            failJoin?.let { throw it }
            return "key-1"
        }
        override fun openPlayer(key: String) { log += "open:$key" }
    }

    private fun TestScope.launcher(fake: Fake) = WatchTogetherLauncher(fake, TestScope(UnconfinedTestDispatcher(testScheduler)))
    private fun ready(l: WatchTogetherLauncher) = l.state.value?.phase as WatchTogether.Phase.Ready

    @Test fun aFilmsRoomIsCreatedShownThenJoinedOnStart() = runTest {
        val fake = Fake()
        val l = launcher(fake)
        l.open("tmdb:movie:603", "The Long Film", RoomSubject.Title)
        assertEquals("The Long Film", l.state.value?.title)
        assertEquals("ABC234", ready(l).room.code)
        assertEquals("http://hub.test:8787/room/ABC234", ready(l).shareUrl)
        // Nothing is joined until Start.
        assertEquals(listOf("create:tmdb:movie:603"), fake.log)
        l.start()
        assertNull(l.state.value)
        assertEquals(listOf("create:tmdb:movie:603", "join:ABC234:false", "open:key-1"), fake.log)
    }

    @Test fun aSeriesAndAChannelAreRoomsOnTheirOwnIds() = runTest {
        // The server resolves the series to its next episode, and makes a channel's room a live one.
        for ((id, subject) in listOf("tmdb:tv:1399" to RoomSubject.Series, "ch:s1:7" to RoomSubject.Channel)) {
            val fake = Fake()
            val l = launcher(fake)
            l.open(id, "Name", subject)
            assertEquals(subject, l.state.value?.subject)
            assertEquals(id, ready(l).room.id)
            assertEquals(listOf("create:$id"), fake.log)
        }
    }

    @Test fun cancellingARoomNobodyJoinedClosesIt() = runTest {
        val fake = Fake()
        val l = launcher(fake)
        l.open("tmdb:movie:603", "The Long Film", RoomSubject.Title)
        l.cancel()
        assertNull(l.state.value)
        assertEquals(listOf("create:tmdb:movie:603", "close:ABC234"), fake.log)
    }

    @Test fun cancellingWhileTheRoomIsBeingMadeClosesItOnceItExists() = runTest {
        val fake = Fake().apply { gate = CompletableDeferred() }
        val l = launcher(fake)
        l.open("tmdb:movie:603", "The Long Film", RoomSubject.Title)
        assertEquals(WatchTogether.Phase.Creating, l.state.value?.phase)
        l.cancel()
        assertNull(l.state.value)
        fake.gate!!.complete(Unit)
        assertNull(l.state.value)
        assertEquals(listOf("create:tmdb:movie:603", "close:ABC234"), fake.log)
    }

    @Test fun aRoomThatCannotBeCreatedSaysWhyAndCanBeTriedAgain() = runTest {
        val fake = Fake().apply { failCreate = IOException("down") }
        val l = launcher(fake)
        l.open("ch:s1:7", "Channel Seven", RoomSubject.Channel)
        val failed = l.state.value?.phase as WatchTogether.Phase.Failed
        assertEquals("Can't reach the server", failed.error.title)
        fake.failCreate = null
        l.retry()
        assertEquals("ABC234", ready(l).room.code)
        assertEquals(RoomSubject.Channel, l.state.value?.subject)
        assertEquals(listOf("create:ch:s1:7", "create:ch:s1:7"), fake.log)
        // A refusal that retrying cannot change is not offered again; closing it leaves no room behind.
        val refused = Fake().apply { failCreate = ApiException(409, ApiError("room_limit", "x"), "x") }
        val second = launcher(refused)
        second.open("tmdb:movie:603", "The Long Film", RoomSubject.Title)
        assertEquals("Too many rooms", (second.state.value?.phase as WatchTogether.Phase.Failed).error.title)
        second.cancel()
        assertNull(second.state.value)
        assertEquals(listOf("create:tmdb:movie:603"), refused.log)
    }

    @Test fun unconfirmedAudioIsAskedAboutOnceThenSentAsConsent() = runTest {
        val fake = Fake().apply { failJoin = ApiException(409, ApiError("audio_selection_required", "x"), "x") }
        val l = launcher(fake)
        l.open("tmdb:movie:603", "The Long Film", RoomSubject.Title)
        l.start()
        assertTrue(ready(l).needsAudioConsent)
        assertNull(ready(l).error)
        assertFalse(ready(l).starting)
        fake.failJoin = null
        l.start()
        assertNull(l.state.value)
        assertEquals(listOf("create:tmdb:movie:603", "join:ABC234:false", "join:ABC234:true", "open:key-1"), fake.log)
    }

    @Test fun aRefusedJoinKeepsTheRoomAndItsCodeOnScreen() = runTest {
        val fake = Fake().apply { failJoin = ApiException(409, ApiError("user_stream_limit", "x"), "x") }
        val l = launcher(fake)
        l.open("tmdb:movie:603", "The Long Film", RoomSubject.Title)
        l.start()
        assertEquals("Already watching", ready(l).error?.title)
        assertEquals("ABC234", ready(l).room.code)
        assertFalse(fake.log.any { it.startsWith("open") || it.startsWith("close") })
    }

    @Test fun aSecondPressWhileOneRoomIsOpenDoesNotMakeAnother() = runTest {
        val fake = Fake()
        val l = launcher(fake)
        l.open("tmdb:movie:603", "The Long Film", RoomSubject.Title)
        l.open("tmdb:movie:604", "Another Film", RoomSubject.Title)
        assertEquals(listOf("create:tmdb:movie:603"), fake.log)
        assertEquals("The Long Film", l.state.value?.title)
    }
}
