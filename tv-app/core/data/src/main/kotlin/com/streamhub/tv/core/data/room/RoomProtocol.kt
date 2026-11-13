package com.streamhub.tv.core.data.room

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.RoomMember
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/** A message from the room's WebSocket. */
sealed interface RoomMessage {
    data class Pong(val t: Long, val serverMs: Long) : RoomMessage
    /**
     * The room clock. [by] is the member who caused it, or null when the room did. [stream] is set
     * only in a room that plays one shared stream: which start of it the room is on now.
     */
    data class State(val clock: RoomClock, val by: String?, val controlsLocked: Boolean, val stream: RoomStream? = null) : RoomMessage
    data class Members(val members: List<RoomMember>) : RoomMessage
    /** The room is paused for this member while they buffer. */
    data class Waiting(val name: String) : RoomMessage
    /** A refused command: controls_locked, host_only, live_no_seek, bad_message. */
    data class Error(val code: String, val message: String) : RoomMessage
    /** The last message before the server closes the socket. */
    data class Closed(val reason: String) : RoomMessage
    /** A type this build does not know, or something that is not a message at all: ignored. */
    data object Unknown : RoomMessage
}

/** The room WebSocket's JSON messages (see RoomJoined in contracts/openapi.yaml). */
object RoomProtocol {
    fun ping(clientMs: Long): String = message("ping") { put("t", clientMs) }
    fun play(): String = message("play")
    fun pause(): String = message("pause")
    fun seek(positionMs: Long): String = message("seek") { put("position_ms", positionMs.coerceAtLeast(0)) }
    fun buffering(on: Boolean): String = message("buffering") { put("on", on) }
    /** Host only. */
    fun lock(on: Boolean): String = message("lock") { put("on", on) }

    private fun message(type: String, fields: kotlinx.serialization.json.JsonObjectBuilder.() -> Unit = {}): String =
        buildJsonObject { put("type", type); fields() }.toString()

    fun decode(text: String): RoomMessage {
        val o = runCatching { StreamHubJson.parseToJsonElement(text).jsonObject }.getOrNull() ?: return RoomMessage.Unknown
        fun string(key: String) = (o[key] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content
        fun long(key: String) = (o[key] as? JsonPrimitive)?.longOrNull
        fun bool(key: String) = (o[key] as? JsonPrimitive)?.booleanOrNull
        return when (string("type")) {
            "pong" -> RoomMessage.Pong(long("t") ?: return RoomMessage.Unknown, long("server_ms") ?: return RoomMessage.Unknown)
            "state" -> RoomMessage.State(
                RoomClock(bool("playing") ?: false, long("position_ms"), long("as_of_ms") ?: return RoomMessage.Unknown),
                by = string("by"),
                controlsLocked = bool("controls_locked") ?: false,
                // Both or neither: an epoch without its time zero cannot position anything.
                stream = long("stream_epoch")?.let { epoch -> long("stream_start_ms")?.let { start -> RoomStream(epoch.toInt(), start.coerceAtLeast(0)) } },
            )
            "members" -> RoomMessage.Members(
                runCatching { StreamHubJson.decodeFromJsonElement(ListSerializer(RoomMember.serializer()), o["members"] ?: return RoomMessage.Unknown) }
                    .getOrElse { return RoomMessage.Unknown },
            )
            "waiting" -> RoomMessage.Waiting(string("name").orEmpty())
            "error" -> RoomMessage.Error(string("code").orEmpty(), string("message").orEmpty())
            "closed" -> RoomMessage.Closed(string("reason").orEmpty())
            else -> RoomMessage.Unknown
        }
    }

    /** `position_ms` of a state as a JSON object, for tests and logs. */
    internal fun stateJson(clock: RoomClock, controlsLocked: Boolean = false, stream: RoomStream? = null): JsonObject = buildJsonObject {
        put("type", "state"); put("playing", clock.playing); put("position_ms", clock.positionMs); put("as_of_ms", clock.asOfMs)
        put("by", null as String?); put("controls_locked", controlsLocked)
        if (stream != null) { put("stream_epoch", stream.epoch); put("stream_start_ms", stream.startMs) }
    }
}

/** Reason used when the room could not be reached for too long, as opposed to one the server gave. */
const val ROOM_CONNECTION_LOST = "connection_lost"

/** Why the room is over, for the person holding the remote. Never retryable: the seat is gone. */
fun roomClosedMessage(reason: String?): UserError = when (reason) {
    "deleted" -> UserError("The host ended the room", "This watch room has been closed for everyone.", canRetry = false)
    "expired" -> UserError("The room has ended", "Watch rooms close when everyone has left or after they have been open for a long time.", canRetry = false)
    "shutdown" -> UserError("The room has ended", "The StreamHub server is restarting, which closes every watch room. Start a new room once it is back.", canRetry = false)
    "silent" -> UserError("Dropped from the room", "The room didn't hear from this TV for too long. Join again with the room's code.", canRetry = false)
    "slow" -> UserError("Dropped from the room", "This TV's connection couldn't keep up with the room. Join again with the room's code.", canRetry = false)
    "replaced" -> UserError("Watching somewhere else", "This seat in the room was taken over by another connection for the same person.", canRetry = false)
    "unauthorized" -> UserError("Seat expired", "This TV's seat in the room is no longer valid. Join again with the room's code.", canRetry = false)
    "not_found" -> UserError("The room has ended", "This watch room no longer exists.", canRetry = false)
    "not_a_member" -> UserError("Dropped from the room", "This TV is no longer in the room. Join again with the room's code.", canRetry = false)
    ROOM_CONNECTION_LOST -> UserError("Lost the room", "This TV couldn't reach the room for too long. Check the network, then join again with the room's code.", canRetry = false)
    else -> UserError("The room has ended", "This watch room was closed.", canRetry = false)
}

/**
 * Whether a closed room connection is worth one more join with the stored member token: the room is
 * still there as far as this TV knows, and only its seat or its connection went (20 s of silence, a
 * member token past its two hours, a connection that could not keep up or was out of reach).
 * Not when the room itself is over, nor when another connection took this seat over on purpose.
 */
fun roomSeatRecoverable(reason: String?): Boolean = reason in setOf("silent", "slow", "unauthorized", "not_a_member", ROOM_CONNECTION_LOST)

/**
 * A refusal of GET /rooms/{code}/descriptor that a join with the member token can mend: the token
 * is past its two hours (401 `room_unauthorized`) or the room dropped the seat (404 `not_a_member`).
 */
fun roomSeatLost(code: String?): Boolean = code == "room_unauthorized" || code == "not_a_member"

/** Text for a command the room refused (`error {code}`); null for refusals the viewer cannot act on. */
fun roomRefusalText(code: String): String? = when (code) {
    "controls_locked" -> "Only the host can control playback"
    "host_only" -> "Only the host can do that"
    "live_no_seek" -> "A live room can't be rewound or skipped"
    else -> null
}
