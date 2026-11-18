package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.model.PlaybackRequest
import com.streamhub.tv.core.data.repo.RoomSubject
import com.streamhub.tv.core.data.room.RoomConnection
import com.streamhub.tv.core.data.room.RoomUiState

/** What the player's "Watch together" opens a room on: the thing playing, its source and its audio track. */
data class RoomStart(
    val titleId: String,
    val title: String,
    val subject: RoomSubject,
    val variantId: String?,
    val audioTrack: Int?,
)

/**
 * The player's "Watch together", decided as the web player decides it (`mayStartRoom` in its menu):
 * offered with the `rooms.create` permission, for a film, an episode or a live channel, and not for
 * a recording, a programme's catch-up, a channel's archive or a rewind, nor while already in a room.
 * Pure, so it is unit-tested.
 */
object RoomStartRules {
    /** null when the item is not offered for this playback. */
    fun roomStart(descriptor: PlaybackDescriptor?, request: PlaybackRequest?, mayCreate: Boolean, inRoom: Boolean): RoomStart? {
        val d = descriptor ?: return null
        if (!mayCreate || inRoom || d.mode == PlaybackMode.HANDOFF) return null
        if (d.id.startsWith("rec:") || d.archiveStart != null) return null
        if (request?.catchupProgrammeId != null || request?.rewindAt != null) return null
        // The variant chosen for this id (its source and audio), when that is what is on screen.
        val variant = request?.takeIf { it.id == d.id }?.variantId
        val channel = d.id.startsWith("ch:")
        return RoomStart(
            titleId = d.id,
            title = listOfNotNull(d.title.takeIf { it.isNotBlank() }, d.subtitle?.takeIf { it.isNotBlank() }).joinToString(" · ").ifBlank { "This title" },
            subject = if (channel) RoomSubject.Channel else RoomSubject.Title,
            variantId = variant,
            audioTrack = d.audioTrack,
        )
    }
}

/**
 * The host who started a room from the player brings what it was watching along: once the room has
 * said where it is, a new film room (paused at the start) is moved to where the TV was and set
 * playing if the TV was playing. A live room plays on by itself. As the web player's `startAtMs`,
 * it is done once and only to a room nobody has moved yet.
 */
data class CarryOver(val positionMs: Long?, val playing: Boolean)

sealed interface CarryStep {
    /** The room has not said where it is yet. */
    data object Wait : CarryStep
    /** Nothing to send: not the host, a live room, or a room someone has already moved. */
    data object Done : CarryStep
    data class Send(val seekToMs: Long?, val play: Boolean) : CarryStep
}

object CarryOverRules {
    /** A position this close to the start is the start: no seek is sent for it. */
    const val MIN_SEEK_MS = 1_000L

    fun step(carry: CarryOver, room: RoomUiState): CarryStep {
        val clock = room.clock
        if (room.connection != RoomConnection.Connected || clock == null) return CarryStep.Wait
        if (!room.isHost || clock.live) return CarryStep.Done
        val untouched = !clock.playing && (clock.positionMs ?: 0) == 0L
        if (!untouched) return CarryStep.Done
        val seek = carry.positionMs?.takeIf { it >= MIN_SEEK_MS }
        return if (seek == null && !carry.playing) CarryStep.Done else CarryStep.Send(seek, carry.playing)
    }
}
