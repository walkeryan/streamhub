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
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
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
 * Watch rooms as the screens that start and join them use them (a title's page, Live TV, "Join a
 * room"); an interface so their logic is tested against a fake.
 */
interface RoomDirectory {
    /** A room for a title, a series (it opens on the next episode the server resolves) or a channel (a live room). */
    suspend fun create(titleId: String): Room
    suspend fun lookup(code: String): Room
    /** Host only: ends the room for everyone. */
    suspend fun close(code: String)
    /** `<server>/room/<code>` for the QR code; null when no server is set. */
    suspend fun shareUrl(room: Room): String?
    /** Takes a seat and returns the player key it is parked under. */
    suspend fun join(code: String, allowUnverifiedAudio: Boolean = false): String
    /** Opens the player on a seat taken with [join]. */
    fun openPlayer(key: String)
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
) : RoomDirectory {
    @Inject constructor(api: StreamHubApi, session: SessionStore, coordinator: PlaybackCoordinator, sockets: KtorRoomSocketFactory) :
        this(api, session, coordinator, sockets as RoomSocketFactory)

    private val seats = ConcurrentHashMap<String, RoomSeat>()

    /**
     * The room this TV last sat in and the member token of that seat. A join of the same room (the
     * code typed again after a dropped connection, say) offers it, so the room keeps the seat if it
     * still holds it; a token it no longer honours, or one from before a switch of person, is ignored
     * by the server and the join takes a new seat.
     */
    @Volatile private var lastSeat: Pair<String, String>? = null

    override suspend fun create(titleId: String): Room = api.createRoom(titleId)
    override suspend fun lookup(code: String): Room = api.room(code)
    override suspend fun close(code: String) = api.deleteRoom(code)
    override suspend fun shareUrl(room: Room): String? = session.current().serverUrl?.let { roomShareUrl(it, room.code, room.joinPath) }

    override suspend fun join(code: String, allowUnverifiedAudio: Boolean): String {
        val seat = seatIn(code, allowUnverifiedAudio)
        val key = coordinator.put(seat.descriptor)
        seats[key] = seat
        return key
    }

    /**
     * Takes up a seat the web app joined (the shell hands over the room's code and the seat's member
     * token): the room keeps that seat and answers with this TV's own descriptor (`client=android_tv`).
     * Returns the player key it is parked under, as [join] does.
     */
    suspend fun joinHeld(code: String, memberToken: String, allowUnverifiedAudio: Boolean): String {
        val seat = seatIn(code, allowUnverifiedAudio, memberToken)
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
        val offered = memberToken ?: lastSeat?.takeIf { it.first.equals(code, ignoreCase = true) }?.second
        val joined = api.joinRoom(code, allowUnverifiedAudio, offered)
        lastSeat = code to joined.memberToken
        return RoomSeat(code, playable(joined.descriptor), joined.memberId, roomSocketUrl(server, joined.wsPath, joined.memberToken), joined.memberToken)
    }

    /** A room plays inside this app or not at all: there is no handing a shared clock to another app. */
    private fun playable(d: PlaybackDescriptor): PlaybackDescriptor {
        if (d.mode == PlaybackMode.HANDOFF || d.url.isNullOrBlank()) {
            throw ApiException(409, ApiError("room_unsupported", "This title cannot be played in a room on this device"), "room_unsupported")
        }
        return d
    }

    override fun openPlayer(key: String) = coordinator.open(key)

    fun seat(key: String): RoomSeat? = seats[key]
    fun client(seat: RoomSeat, isHost: Boolean = false): RoomClient = RoomClient(seat.socketUrl, seat.memberId, sockets, isHost = isHost)
    fun release(key: String) { seats.remove(key) }
}

@Module
@InstallIn(SingletonComponent::class)
abstract class RoomModule {
    @Binds abstract fun rooms(impl: RoomRepository): RoomDirectory
}
