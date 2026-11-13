package com.streamhub.tv.core.data.room

import com.streamhub.tv.core.data.model.RoomMember
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

enum class RoomConnection { Connecting, Connected, Reconnecting, Closed }

/** What the player shows of the room. */
data class RoomUiState(
    val members: List<RoomMember> = emptyList(),
    /** The member the room is paused for while they buffer. */
    val waitingFor: String? = null,
    /** Only the host's play, pause and seek apply. */
    val controlsLocked: Boolean = false,
    val isHost: Boolean = false,
    val connection: RoomConnection = RoomConnection.Connecting,
    /** Once [connection] is Closed: the server's reason, or [ROOM_CONNECTION_LOST]. */
    val closedReason: String? = null,
    /** The room clock; null until the first `state` message. */
    val clock: RoomClock? = null,
    /** The shared stream the last `state` named; null in a room without one (and until the first `state`). */
    val stream: RoomStream? = null,
)

/** One open room WebSocket, reduced to what [RoomClient] needs so tests can stand in for the network. */
interface RoomSocket {
    /** Text messages until the connection ends, however it ends. */
    fun incoming(): Flow<String>
    suspend fun send(text: String)
    /** A close frame. 1000 leaves the room at once; the server keeps the seat for 20 s after any other end. */
    suspend fun close(code: Short)
    /** Drops the connection without a close frame, keeping the seat. */
    fun abort()
    /** The code of the close frame the server sent, if it sent one. */
    suspend fun closeCode(): Short?
}

fun interface RoomSocketFactory {
    suspend fun open(url: String): RoomSocket
}

/**
 * One member's connection to a watch room: speaks the room protocol, estimates the server clock from
 * ping/pong, and reconnects with the same member token (1, 2, 4, 8 s, then every 15 s) until the
 * server says the room or the seat is gone, or the room has been out of reach for [GIVE_UP_MS].
 *
 * [run] does the work and returns when the room is over; cancelling it leaves the room.
 */
class RoomClient(
    private val url: String,
    private val memberId: String,
    private val sockets: RoomSocketFactory,
    private val nowMs: () -> Long = System::currentTimeMillis,
    isHost: Boolean = false,
) {
    private val _state = MutableStateFlow(RoomUiState(isHost = isHost))
    val state: StateFlow<RoomUiState> = _state.asStateFlow()

    private val _refusals = MutableSharedFlow<RoomMessage.Error>(extraBufferCapacity = 4)
    /** Commands the room refused (`error {code, message}`). */
    val refusals: SharedFlow<RoomMessage.Error> = _refusals

    private val clock = ClockOffset()
    val clockSynced: Boolean get() = clock.synced
    /** Our best estimate of the server's clock right now. */
    fun serverNowMs(): Long = clock.serverNow(nowMs())

    @Volatile private var socket: RoomSocket? = null
    @Volatile private var lastHeardMs = 0L
    @Volatile private var keepSeat = false

    /**
     * From now on cancelling [run] drops the connection without a close frame, so the room keeps this
     * seat (for 20 s) instead of giving it up at once. Called before joining again with the member
     * token, which is how the same seat is taken up by a new connection.
     */
    fun keepSeatOnExit() { keepSeat = true }

    suspend fun run() {
        var attempt = 0
        var unreachableSince: Long? = null
        while (true) {
            val opened = try { sockets.open(url) }
                catch (e: CancellationException) { throw e }
                catch (_: Exception) { null }
            if (opened != null) {
                attempt = 0
                unreachableSince = null
                val end = serve(opened)
                if (end != null) { _state.update { it.copy(connection = RoomConnection.Closed, closedReason = end, waitingFor = null) }; return }
            }
            val since = unreachableSince ?: nowMs().also { unreachableSince = it }
            if (nowMs() - since >= GIVE_UP_MS) {
                _state.update { it.copy(connection = RoomConnection.Closed, closedReason = ROOM_CONNECTION_LOST, waitingFor = null) }
                return
            }
            _state.update { it.copy(connection = RoomConnection.Reconnecting) }
            delay(RoomBackoff.delayMs(attempt++))
        }
    }

    /** Runs one connection. Returns why the room is over, or null when only the connection ended (reconnect). */
    private suspend fun serve(s: RoomSocket): String? {
        var closed: String? = null
        var graceful = false
        socket = s
        lastHeardMs = nowMs()
        // The server sends members, state and (if it applies) waiting again on every connect.
        _state.update { it.copy(connection = RoomConnection.Connected, waitingFor = null) }
        try {
            coroutineScope {
                val keepAlive = launch {
                    while (true) {
                        // Nothing heard for three pings: the connection is dead without having said so.
                        if (nowMs() - lastHeardMs > SILENCE_MS) { s.abort(); break }
                        try { s.send(RoomProtocol.ping(nowMs())) } catch (e: CancellationException) { throw e } catch (_: Exception) { s.abort(); break }
                        delay(PING_MS)
                    }
                }
                try {
                    s.incoming().collect { text ->
                        lastHeardMs = nowMs()
                        when (val m = RoomProtocol.decode(text)) {
                            is RoomMessage.Pong -> clock.onPong(m.t, nowMs(), m.serverMs)
                            // Any state clears a waiting notice.
                            is RoomMessage.State -> _state.update { it.copy(clock = m.clock, stream = m.stream, controlsLocked = m.controlsLocked, waitingFor = null) }
                            is RoomMessage.Members -> _state.update { st ->
                                st.copy(members = m.members, isHost = m.members.firstOrNull { it.id == memberId }?.host ?: st.isHost)
                            }
                            is RoomMessage.Waiting -> _state.update { it.copy(waitingFor = m.name.ifBlank { "someone" }) }
                            is RoomMessage.Error -> _refusals.tryEmit(m)
                            is RoomMessage.Closed -> closed = m.reason.ifBlank { "closed" }
                            RoomMessage.Unknown -> Unit
                        }
                    }
                } catch (e: CancellationException) { throw e }
                catch (_: Exception) { /* the connection broke: same as it ending */ }
                keepAlive.cancel()
            }
            graceful = true
        } finally {
            socket = null
            // Cancelled from outside. The viewer left or the screen closed: give the seat back at once.
            // About to join again with the member token: no close frame, so the seat is still there to keep.
            if (!graceful) withContext(NonCancellable) {
                if (!keepSeat) withTimeoutOrNull(CLOSE_WAIT_MS) { runCatching { s.close(CLOSE_NORMAL) } }
                s.abort()
            }
        }
        closed?.let { return it }
        return when (withTimeoutOrNull(CLOSE_WAIT_MS) { runCatching { s.closeCode() }.getOrNull() }) {
            CLOSE_ROOM_OVER -> "closed"
            CLOSE_UNAUTHORIZED -> "unauthorized"
            CLOSE_NOT_FOUND -> "not_found"
            else -> null
        }
    }

    suspend fun play(): Boolean = send(RoomProtocol.play())
    suspend fun pause(): Boolean = send(RoomProtocol.pause())
    suspend fun seek(positionMs: Long): Boolean = send(RoomProtocol.seek(positionMs))
    suspend fun buffering(on: Boolean): Boolean = send(RoomProtocol.buffering(on))
    suspend fun lock(on: Boolean): Boolean = send(RoomProtocol.lock(on))

    /** false when there is no connection to send on: a command is never queued and replayed late. */
    private suspend fun send(text: String): Boolean {
        val s = socket ?: return false
        return try { s.send(text); true } catch (e: CancellationException) { throw e } catch (_: Exception) { false }
    }

    companion object {
        /** A member must be heard from at least every 20 s; a ping every 5 s leaves room for three to go missing. */
        const val PING_MS = 5_000L
        const val SILENCE_MS = 16_000L
        /** The seat is dropped after 20 s of silence, so a room out of reach this long is gone for this TV. */
        const val GIVE_UP_MS = 60_000L
        const val CLOSE_WAIT_MS = 1_500L
        const val CLOSE_NORMAL: Short = 1000
        const val CLOSE_ROOM_OVER: Short = 4000
        const val CLOSE_UNAUTHORIZED: Short = 4401
        const val CLOSE_NOT_FOUND: Short = 4404
    }
}
