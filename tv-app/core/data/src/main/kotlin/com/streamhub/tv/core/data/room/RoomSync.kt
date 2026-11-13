package com.streamhub.tv.core.data.room

import kotlin.math.abs

/** The room's shared clock as last sent by the server. [positionMs] is null in a live room. */
data class RoomClock(val playing: Boolean, val positionMs: Long?, val asOfMs: Long) {
    val live: Boolean get() = positionMs == null
}

/** What this player has to do to agree with the room. */
sealed interface SyncAction {
    data object None : SyncAction
    data object Play : SyncAction
    data object Pause : SyncAction
    data class SeekTo(val positionMs: Long) : SyncAction
}

/**
 * Keeps one player in step with the room, as a pure function so the rules are unit-tested.
 * One action per call; the caller applies it and asks again on its next tick, so a member who is
 * both paused and in the wrong place is first lined up and then started.
 */
object RoomSync {
    /** A member further than this from the room position seeks; smaller errors are left alone. */
    const val DRIFT_MS = 1_000L

    /**
     * Where the room is at server time [serverNowMs]; null in a live room. A state stamped later than
     * our estimate of the server's clock (the estimate was corrected, or the message overtook a pong)
     * counts as "just now": the position never runs backwards from what the server said.
     */
    fun positionAt(state: RoomClock, serverNowMs: Long): Long? {
        val base = (state.positionMs ?: return null).coerceAtLeast(0)
        if (!state.playing) return base
        return base + (serverNowMs - state.asOfMs).coerceAtLeast(0)
    }

    fun decide(roomState: RoomClock, serverNowMs: Long, localPositionMs: Long, localPlaying: Boolean): SyncAction {
        // A paused room stops first: every moment of playing on is a moment further from where it paused.
        if (!roomState.playing && localPlaying) return SyncAction.Pause
        val target = positionAt(roomState, serverNowMs)
        if (target != null && abs(localPositionMs - target) > DRIFT_MS) return SyncAction.SeekTo(target)
        if (roomState.playing && !localPlaying) return SyncAction.Play
        return SyncAction.None
    }
}

/**
 * Estimates the server's clock from ping/pong: offset = server_ms - (sent + received) / 2. The
 * sample with the smallest round trip is kept, since that is the one whose two legs can differ least.
 */
class ClockOffset {
    private var bestRoundTripMs = Long.MAX_VALUE

    /** Server time minus local time. 0 until the first pong. */
    var offsetMs: Long = 0
        private set

    val synced: Boolean get() = bestRoundTripMs != Long.MAX_VALUE

    fun onPong(sentAtMs: Long, receivedAtMs: Long, serverMs: Long) {
        val roundTrip = receivedAtMs - sentAtMs
        // A negative round trip means the local clock was changed between the two readings.
        if (roundTrip < 0 || roundTrip > bestRoundTripMs) return
        bestRoundTripMs = roundTrip
        offsetMs = serverMs - (sentAtMs + receivedAtMs) / 2
    }

    fun serverNow(localNowMs: Long): Long = localNowMs + offsetMs
}

/** Reconnect delays: 1, 2, 4, 8 s, then 15 s each time. */
object RoomBackoff {
    const val MAX_MS = 15_000L
    fun delayMs(attempt: Int): Long = if (attempt >= 4) MAX_MS else minOf(MAX_MS, 1_000L shl attempt.coerceAtLeast(0))
}
