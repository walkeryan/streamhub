package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.model.Room
import com.streamhub.tv.core.data.room.KtorRoomSocketFactory
import com.streamhub.tv.core.data.room.RoomClient
import com.streamhub.tv.core.data.room.RoomSocketFactory
import com.streamhub.tv.core.data.room.roomShareUrl
import com.streamhub.tv.core.data.room.roomSocketUrl
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/** A seat this TV holds in a watch room, handed from the screen that joined to the player. */
class RoomSeat internal constructor(
    val code: String,
    val descriptor: PlaybackDescriptor,
    val memberId: String,
    internal val socketUrl: String,
    /** Valid two hours, for this room and member only: opens the socket, fetches the descriptor, keeps the seat on a rejoin. */
    internal val memberToken: String,
) {
    override fun toString(): String = "RoomSeat(code=$code, memberId=$memberId)"
}

/**
 * Watch rooms: create, look up, join, close. Joining resolves the stream for this profile and
 * parks the seat under a player key, exactly as a pressed Play parks its descriptor, so the player
 * opens with no screen in between and finds out from [seat] that it is in a room.
 */
@Singleton
class RoomRepository internal constructor(
    private val api: StreamHubApi,
    private val session: SessionStore,
    private val coordinator: PlaybackCoordinator,
    private val sockets: RoomSocketFactory,
) {
    @Inject constructor(api: StreamHubApi, session: SessionStore, coordinator: PlaybackCoordinator, sockets: KtorRoomSocketFactory) :
        this(api, session, coordinator, sockets as RoomSocketFactory)

    private val seats = ConcurrentHashMap<String, RoomSeat>()

    suspend fun create(titleId: String): Room = api.createRoom(titleId)
    suspend fun lookup(code: String): Room = api.room(code)
    /** Host only: ends the room for everyone. */
    suspend fun close(code: String) = api.deleteRoom(code)

    /** `<server>/room/<code>` for the QR code; null when no server is set. */
    suspend fun shareUrl(room: Room): String? = session.current().serverUrl?.let { roomShareUrl(it, room.code, room.joinPath) }

    /** Takes a seat and returns the player key it is parked under. */
    suspend fun join(code: String, allowUnverifiedAudio: Boolean = false): String {
        val seat = seatIn(code, allowUnverifiedAudio)
        val key = coordinator.put(seat.descriptor)
        seats[key] = seat
        return key
    }

    /**
     * Joins again for a player that is already open, with the member token of the seat it holds: the
     * room keeps that seat (and hands out a fresh token) while it still honours the old one, and
     * gives a new seat otherwise. Either way the descriptor is the room's stream as it is now.
     */
    suspend fun rejoin(key: String, held: RoomSeat): RoomSeat =
        seatIn(held.code, allowUnverifiedAudio = true, memberToken = held.memberToken).also { seats[key] = it }

    /**
     * This member's descriptor as it is now (GET /rooms/{code}/descriptor); seat and socket are
     * untouched. For a member that lost its place: a player error, a stream address that expired, or
     * a shared stream the room has started again since (a new `stream_epoch`).
     */
    suspend fun descriptor(seat: RoomSeat): PlaybackDescriptor =
        playable(api.roomDescriptor(seat.code, seat.memberToken, allowUnverifiedAudio = true))

    private suspend fun seatIn(code: String, allowUnverifiedAudio: Boolean, memberToken: String? = null): RoomSeat {
        val server = session.current().serverUrl ?: throw ApiException(0, null, "Server URL not set")
        val joined = api.joinRoom(code, allowUnverifiedAudio, memberToken)
        return RoomSeat(code, playable(joined.descriptor), joined.memberId, roomSocketUrl(server, joined.wsPath, joined.memberToken), joined.memberToken)
    }

    /** A room plays inside this app or not at all: there is no handing a shared clock to another app. */
    private fun playable(d: PlaybackDescriptor): PlaybackDescriptor {
        if (d.mode == PlaybackMode.HANDOFF || d.url.isNullOrBlank()) {
            throw ApiException(409, ApiError("room_unsupported", "This title cannot be played in a room on this device"), "room_unsupported")
        }
        return d
    }

    /** Opens the player on a seat taken with [join]. */
    fun openPlayer(key: String) = coordinator.open(key)

    fun seat(key: String): RoomSeat? = seats[key]
    fun client(seat: RoomSeat, isHost: Boolean = false): RoomClient = RoomClient(seat.socketUrl, seat.memberId, sockets, isHost = isHost)
    fun release(key: String) { seats.remove(key) }
}
