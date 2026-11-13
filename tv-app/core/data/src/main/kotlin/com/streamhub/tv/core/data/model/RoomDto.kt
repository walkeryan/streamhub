package com.streamhub.tv.core.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/* Watch rooms (POST /rooms, GET/DELETE /rooms/{code}, POST /rooms/{code}/join). */

@Serializable
data class RoomCreate(val id: String, @SerialName("variant_id") val variantId: String? = null)

@Serializable
data class RoomJoinRequest(
    val client: String = "android_tv",
    /** Consent to the room's source when its audio is unverified or not in this profile's languages. */
    @SerialName("allow_unverified_audio") val allowUnverifiedAudio: Boolean = false,
)

@Serializable
data class RoomTitle(
    val id: String = "",
    /** A MediaKind on the wire; kept as text so a kind this build does not know still decodes. */
    val kind: String? = null,
    val title: String = "",
    val subtitle: String? = null,
    val year: Int? = null,
    val poster: String? = null,
    val backdrop: String? = null,
)

@Serializable
data class RoomMember(
    val id: String = "",
    val name: String = "",
    val host: Boolean = false,
    val buffering: Boolean = false,
)

/** The room clock: while playing, the position now is position_ms + (server time now - as_of_ms). */
@Serializable
data class RoomState(
    val playing: Boolean = false,
    /** null in a live room: a channel has no position. */
    @SerialName("position_ms") val positionMs: Long? = null,
    @SerialName("as_of_ms") val asOfMs: Long = 0,
)

@Serializable
data class Room(
    val code: String = "",
    /** Web path of the join screen: /room/<code>. */
    @SerialName("join_path") val joinPath: String? = null,
    val id: String = "",
    val live: Boolean = false,
    @SerialName("host_name") val hostName: String = "",
    @SerialName("is_host") val isHost: Boolean = false,
    @SerialName("controls_locked") val controlsLocked: Boolean = false,
    @SerialName("waiting_for") val waitingFor: String? = null,
    @SerialName("max_members") val maxMembers: Int = 0,
    val members: List<RoomMember> = emptyList(),
    val state: RoomState = RoomState(),
    @SerialName("server_ms") val serverMs: Long = 0,
    val title: RoomTitle = RoomTitle(),
)

/** A seat in the room: the stream to play and the token that opens the room's WebSocket. */
@Serializable
data class RoomJoined(
    @SerialName("member_token") val memberToken: String,
    @SerialName("member_id") val memberId: String = "",
    @SerialName("expires_in_seconds") val expiresInSeconds: Int = 0,
    /** /api/v1/rooms/<code>/ws */
    @SerialName("ws_path") val wsPath: String,
    val descriptor: PlaybackDescriptor,
) {
    override fun toString(): String = "RoomJoined(memberToken=[REDACTED], memberId=$memberId, wsPath=$wsPath, descriptor=${descriptor.id})"
}
