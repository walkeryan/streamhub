package com.streamhub.tv.core.data.room

import com.streamhub.tv.core.data.model.PlaybackDescriptor
import kotlin.math.abs

/** The room's shared clock as last sent by the server. [positionMs] is null in a live room. */
data class RoomClock(val playing: Boolean, val positionMs: Long?, val asOfMs: Long) {
    val live: Boolean get() = positionMs == null
}

/**
 * The one stream a room plays to all its members, when it has one: [epoch] counts the stream's
 * starts (a seek starts it again), and [startMs] is where in the title its media time zero is.
 */
data class RoomStream(val epoch: Int, val startMs: Long)

/** The shared stream a room descriptor is a seat on; null for a descriptor of the member's own stream. */
fun PlaybackDescriptor.roomStream(): RoomStream? = streamEpoch?.let { RoomStream(it, (startPositionMs ?: 0).coerceAtLeast(0)) }

/** How the stream a member is playing stands to the one the room's last `state` named. */
enum class StreamStep {
    /** The same start of the stream, or a room without a shared stream: seek inside what is playing. */
    Same,
    /** The room has started its stream again since: load it again and take its new time zero. */
    Reload,
    /** This member's descriptor is from a later start than the last `state`: that `state` is on its way. */
    Wait,
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

    /**
     * Compares the epoch of a `state` with the one being played (the descriptor's, then the last
     * `state`'s). A `state` without an epoch is a title each member plays for themselves, or a live
     * channel: there is nothing to reload. Epochs only count up, so a `state` behind the descriptor
     * is an old one whose successor has not arrived yet.
     */
    fun streamStep(playing: RoomStream?, said: RoomStream?): StreamStep = when {
        said == null -> StreamStep.Same
        playing == null || said.epoch > playing.epoch -> StreamStep.Reload
        said.epoch < playing.epoch -> StreamStep.Wait
        else -> StreamStep.Same
    }

    /**
     * The player's own position for a position in the title. A shared stream's media time zero is
     * its `stream_start_ms`, so the player is at `position_ms - stream_start_ms`; a member's own
     * stream covers the whole title and the two are the same.
     */
    fun playerPosition(titlePositionMs: Long, stream: RoomStream?): Long = (titlePositionMs - (stream?.startMs ?: 0)).coerceAtLeast(0)

    /** The position in the title for a position of the player: the inverse of [playerPosition]. */
    fun titlePosition(playerPositionMs: Long, stream: RoomStream?): Long = playerPositionMs.coerceAtLeast(0) + (stream?.startMs ?: 0)

    /**
     * @param localPositionMs the player's own position, on the stream it is playing
     * @param stream the shared stream being played, when the room has one
     * @return [SyncAction.SeekTo] carries a position of the player (see [playerPosition]), not of the title
     */
    fun decide(roomState: RoomClock, serverNowMs: Long, localPositionMs: Long, localPlaying: Boolean, stream: RoomStream? = null): SyncAction {
        // A paused room stops first: every moment of playing on is a moment further from where it paused.
        if (!roomState.playing && localPlaying) return SyncAction.Pause
        val target = positionAt(roomState, serverNowMs)?.let { playerPosition(it, stream) }
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
