package com.streamhub.tv.core.data.room

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.launch
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
class RoomClientTest {
    private class FakeSocket : RoomSocket {
        private val inbox = Channel<String>(Channel.UNLIMITED)
        val sent = mutableListOf<String>()
        var serverCloseCode: Short? = null
        var closedWith: Short? = null
        var aborted = false
        override fun incoming(): Flow<String> = inbox.receiveAsFlow()
        override suspend fun send(text: String) { if (aborted || closedWith != null || inbox.isClosedForSend) throw IOException("closed"); sent += text }
        override suspend fun close(code: Short) { closedWith = code; inbox.close() }
        override fun abort() { aborted = true; inbox.close() }
        override suspend fun closeCode(): Short? = serverCloseCode

        fun says(json: String) { inbox.trySend(json) }
        /** The connection ends: with a close frame when [code] is set, without one (network drop) otherwise. */
        fun ends(code: Short? = null, reason: String? = null) {
            reason?.let { says("""{"type":"closed","reason":"$it"}""") }
            serverCloseCode = code
            inbox.close()
        }
        fun commands() = sent.filterNot { it.contains("\"ping\"") }
    }

    /** Hands out the scripted sockets in order; null (or running out) is a failed connect. */
    private class Script(private val scope: TestScope, vararg sockets: FakeSocket?) : RoomSocketFactory {
        private val queue = ArrayDeque(sockets.toList())
        val openedAt = mutableListOf<Long>()
        val urls = mutableListOf<String>()
        override suspend fun open(url: String): RoomSocket {
            openedAt += scope.testScheduler.currentTime
            urls += url
            return queue.removeFirstOrNull() ?: throw IOException("unreachable")
        }
    }

    private val url = "ws://hub.test:8787/api/v1/rooms/ABC234/ws?member_token=t"
    private fun TestScope.client(script: Script) = RoomClient(url, memberId = "m2", sockets = script, nowMs = { testScheduler.currentTime })
    private fun TestScope.start(client: RoomClient): Job = backgroundScope.launch { client.run() }.also { runCurrent() }

    @Test fun onConnectTheRoomIsMirroredFromMembersStateAndWaiting() = runTest {
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        assertEquals(RoomConnection.Connecting, client.state.value.connection)
        start(client)
        assertEquals(RoomConnection.Connected, client.state.value.connection)
        socket.says("""{"type":"members","members":[{"id":"m1","name":"Alex","host":true,"buffering":false},{"id":"m2","name":"Sam","host":false,"buffering":true}]}""")
        socket.says("""{"type":"state","playing":false,"position_ms":42000,"as_of_ms":900,"by":null,"controls_locked":true}""")
        socket.says("""{"type":"waiting","name":"Sam"}""")
        runCurrent()
        val s = client.state.value
        assertEquals(listOf("Alex", "Sam"), s.members.map { it.name })
        assertFalse(s.isHost)
        assertTrue(s.controlsLocked)
        assertEquals(RoomClock(false, 42_000, 900), s.clock)
        assertEquals("Sam", s.waitingFor)
        // Any state clears the waiting notice.
        socket.says("""{"type":"state","playing":true,"position_ms":42000,"as_of_ms":1500,"by":null,"controls_locked":false}""")
        runCurrent()
        assertNull(client.state.value.waitingFor)
        assertFalse(client.state.value.controlsLocked)
    }

    @Test fun hostIsWhoeverTheMemberListSaysThisSeatIs() = runTest {
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        start(client)
        socket.says("""{"type":"members","members":[{"id":"m2","name":"Sam","host":true,"buffering":false}]}""")
        runCurrent()
        assertTrue(client.state.value.isHost)
    }

    @Test fun pingsEveryFiveSecondsAndLearnsTheServerClock() = runTest {
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        advanceTimeBy(1_000)
        start(client)
        assertEquals(listOf("""{"type":"ping","t":1000}"""), socket.sent)
        assertFalse(client.clockSynced)
        advanceTimeBy(40)   // the pong arrives 40 ms later; the server read its clock in the middle
        socket.says("""{"type":"pong","t":1000,"server_ms":501020}""")
        runCurrent()
        assertTrue(client.clockSynced)
        assertEquals(500_000L + testScheduler.currentTime, client.serverNowMs())
        advanceTimeBy(4_960)
        runCurrent()
        assertEquals(2, socket.sent.size)
        assertEquals("""{"type":"ping","t":6000}""", socket.sent[1])
    }

    @Test fun reconnectsWithTheSameTokenAfterOneTwoFourEightSeconds() = runTest {
        val first = FakeSocket()
        val second = FakeSocket()
        val script = Script(this, first, null, null, null, second)
        val client = client(script)
        start(client)
        first.says("""{"type":"state","playing":true,"position_ms":1000,"as_of_ms":0,"by":null,"controls_locked":false}""")
        first.says("""{"type":"waiting","name":"Alex"}""")
        runCurrent()
        first.ends()    // network drop: no close frame, no closed message
        runCurrent()
        assertEquals(RoomConnection.Reconnecting, client.state.value.connection)
        advanceTimeBy(15_001)
        runCurrent()
        assertEquals(listOf(0L, 1_000L, 3_000L, 7_000L, 15_000L), script.openedAt)
        assertEquals(List(5) { url }, script.urls)
        assertEquals(RoomConnection.Connected, client.state.value.connection)
        // The clock survives the gap; the waiting notice is the server's to send again.
        assertEquals(RoomClock(true, 1_000, 0), client.state.value.clock)
        assertNull(client.state.value.waitingFor)
        assertNull(client.state.value.closedReason)
    }

    @Test fun aClosedMessageEndsTheRoomWithoutReconnecting() = runTest {
        val socket = FakeSocket()
        val script = Script(this, socket, FakeSocket())
        val client = client(script)
        val job = start(client)
        socket.ends(code = 4000, reason = "deleted")
        runCurrent()
        assertEquals(RoomConnection.Closed, client.state.value.connection)
        assertEquals("deleted", client.state.value.closedReason)
        assertTrue(job.isCompleted)
        advanceTimeBy(60_000)
        assertEquals(1, script.openedAt.size)
    }

    @Test fun aCloseCodeAloneAlsoEndsTheRoom() = runTest {
        for ((code, reason) in listOf(4404.toShort() to "not_found", 4401.toShort() to "unauthorized", 4000.toShort() to "closed")) {
            val socket = FakeSocket()
            val client = client(Script(this, socket))
            start(client)
            socket.ends(code = code)
            runCurrent()
            assertEquals(reason, client.state.value.closedReason)
        }
    }

    @Test fun anOrdinaryCloseCodeIsJustADroppedConnection() = runTest {
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        start(client)
        socket.ends(code = 1006)
        runCurrent()
        assertEquals(RoomConnection.Reconnecting, client.state.value.connection)
    }

    @Test fun aRoomOutOfReachForAMinuteIsGivenUp() = runTest {
        val socket = FakeSocket()
        val script = Script(this, socket)
        val client = client(script)
        val job = start(client)
        socket.ends()
        advanceTimeBy(59_999)
        runCurrent()
        assertEquals(RoomConnection.Reconnecting, client.state.value.connection)
        advanceTimeBy(2)
        runCurrent()
        assertEquals(RoomConnection.Closed, client.state.value.connection)
        assertEquals(ROOM_CONNECTION_LOST, client.state.value.closedReason)
        assertTrue(job.isCompleted)
        // 0 (connected), then 1, 3, 7, 15, 30, 45 and 60 s.
        assertEquals(listOf(0L, 1_000L, 3_000L, 7_000L, 15_000L, 30_000L, 45_000L, 60_000L), script.openedAt)
    }

    @Test fun commandsGoOutOnlyWhileConnectedAndAreNeverQueued() = runTest {
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        assertFalse(client.play())
        start(client)
        assertTrue(client.play())
        assertTrue(client.seek(90_000))
        assertTrue(client.pause())
        assertTrue(client.buffering(true))
        assertTrue(client.lock(true))
        assertEquals(
            listOf("""{"type":"play"}""", """{"type":"seek","position_ms":90000}""", """{"type":"pause"}""", """{"type":"buffering","on":true}""", """{"type":"lock","on":true}"""),
            socket.commands(),
        )
        socket.ends()
        runCurrent()
        assertFalse(client.pause())
        assertEquals(5, socket.commands().size)
    }

    @Test fun refusedCommandsAreReported() = runTest {
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        val seen = mutableListOf<RoomMessage.Error>()
        backgroundScope.launch { client.refusals.collect { seen += it } }
        start(client)
        socket.says("""{"type":"error","code":"controls_locked","message":"Only the host can control playback"}""")
        runCurrent()
        assertEquals(listOf(RoomMessage.Error("controls_locked", "Only the host can control playback")), seen)
    }

    @Test fun leavingSendsANormalCloseSoTheSeatIsFreedAtOnce() = runTest {
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        val job = start(client)
        job.cancel()
        runCurrent()
        assertEquals(1000.toShort(), socket.closedWith)
    }

    @Test fun keepingTheSeatDropsTheConnectionWithoutACloseFrame() = runTest {
        // Before a join with the member token: the room must still hold the seat for that join to keep.
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        val job = start(client)
        client.keepSeatOnExit()
        job.cancel()
        runCurrent()
        assertNull(socket.closedWith)
        assertTrue(socket.aborted)
    }

    @Test fun everyStateSaysWhichStartOfTheSharedStreamTheRoomIsOn() = runTest {
        val first = FakeSocket()
        val second = FakeSocket()
        val client = client(Script(this, first, second))
        start(client)
        assertNull(client.state.value.stream)
        first.says("""{"type":"state","playing":true,"position_ms":1000,"as_of_ms":900,"by":null,"controls_locked":false,"stream_epoch":1,"stream_start_ms":0}""")
        runCurrent()
        assertEquals(RoomStream(1, 0), client.state.value.stream)
        // A seek restarts the stream; its state carries the new epoch and time zero.
        first.says("""{"type":"state","playing":true,"position_ms":3602000,"as_of_ms":950,"by":"m1","controls_locked":false,"stream_epoch":2,"stream_start_ms":3600000}""")
        runCurrent()
        assertEquals(RoomStream(2, 3_600_000), client.state.value.stream)
        assertEquals(3_602_000L, client.state.value.clock?.positionMs)
        // A seek missed while disconnected is learned from the state the server sends on reconnect.
        first.ends()
        runCurrent()
        advanceTimeBy(1_000)
        runCurrent()
        second.says("""{"type":"state","playing":false,"position_ms":60000,"as_of_ms":990,"by":null,"controls_locked":false,"stream_epoch":3,"stream_start_ms":60000}""")
        runCurrent()
        assertEquals(RoomStream(3, 60_000), client.state.value.stream)
    }

    @Test fun aRoomWithoutASharedStreamNamesNone() = runTest {
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        start(client)
        socket.says("""{"type":"state","playing":true,"position_ms":1000,"as_of_ms":900,"by":null,"controls_locked":false}""")
        runCurrent()
        assertNull(client.state.value.stream)
        assertEquals(1_000L, client.state.value.clock?.positionMs)
    }

    @Test fun aConnectionThatGoesQuietIsDroppedWithoutLeavingAndReopened() = runTest {
        val quiet = FakeSocket()
        val next = FakeSocket()
        val script = Script(this, quiet, next)
        val client = client(script)
        start(client)
        advanceTimeBy(20_001)   // pings go out at 0, 5, 10, 15 s and nothing ever comes back
        runCurrent()
        assertTrue(quiet.aborted)
        assertNull(quiet.closedWith)
        advanceTimeBy(1_000)
        runCurrent()
        assertEquals(2, script.openedAt.size)
        assertEquals(RoomConnection.Connected, client.state.value.connection)
    }

    @Test fun unknownMessagesDoNotDisturbTheRoom() = runTest {
        val socket = FakeSocket()
        val client = client(Script(this, socket))
        start(client)
        socket.says("garbage")
        socket.says("""{"type":"reaction","emoji":"x"}""")
        socket.says("""{"type":"waiting","name":""}""")
        runCurrent()
        assertEquals(RoomConnection.Connected, client.state.value.connection)
        assertEquals("someone", client.state.value.waitingFor)
    }
}
