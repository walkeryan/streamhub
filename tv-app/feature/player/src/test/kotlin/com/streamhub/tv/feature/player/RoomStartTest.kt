package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.model.PlaybackRequest
import com.streamhub.tv.core.data.repo.RoomSubject
import com.streamhub.tv.core.data.room.RoomClock
import com.streamhub.tv.core.data.room.RoomConnection
import com.streamhub.tv.core.data.room.RoomUiState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The player menu's "Watch together": when it is offered, what the room is made on, and how the host carries on. */
class RoomStartTest {
    private fun film(id: String = "lib:s1:42", audioTrack: Int? = null) =
        PlaybackDescriptor(id = id, mode = PlaybackMode.DIRECT, url = "http://hub.test/stream/a", title = "The Long Film", audioTrack = audioTrack, durationMs = 7_200_000)
    private val channel = PlaybackDescriptor(id = "ch:s1:7", mode = PlaybackMode.HLS, url = "http://hub.test/live/7.m3u8", live = true, title = "Channel Seven")

    @Test fun aFilmIsOfferedOnItsIdSourceAndAudioTrack() {
        val start = RoomStartRules.roomStart(film(audioTrack = 2), PlaybackRequest("lib:s1:42", variantId = "lib:s1:42"), mayCreate = true, inRoom = false)
        assertEquals(RoomStart("lib:s1:42", "The Long Film", RoomSubject.Title, variantId = "lib:s1:42", audioTrack = 2), start)
    }

    @Test fun anEpisodeIsNamedWithItsSubtitleAndAChannelIsALiveRoom() {
        val episode = film("lib:s1:77").copy(title = "A Show", subtitle = "S1 E2 · The Second One")
        assertEquals("A Show · S1 E2 · The Second One", RoomStartRules.roomStart(episode, null, mayCreate = true, inRoom = false)?.title)
        val live = RoomStartRules.roomStart(channel, PlaybackRequest("ch:s1:7"), mayCreate = true, inRoom = false)
        assertEquals(RoomSubject.Channel, live?.subject)
        assertEquals("ch:s1:7", live?.titleId)
        assertNull(live?.variantId)
    }

    @Test fun aVariantChosenForSomethingElseIsNotCarriedOver() {
        // A series was asked for and the server played an episode: the variant named was the series'.
        val start = RoomStartRules.roomStart(film("lib:s1:77"), PlaybackRequest("tmdb:tv:1399", variantId = "lib:s1:9"), mayCreate = true, inRoom = false)
        assertEquals("lib:s1:77", start?.titleId)
        assertNull(start?.variantId)
    }

    @Test fun notOfferedWithoutPermissionInARoomOrForWhatTheWebPlayerLeavesOut() {
        val request = PlaybackRequest("lib:s1:42")
        assertNull(RoomStartRules.roomStart(film(), request, mayCreate = false, inRoom = false))
        assertNull(RoomStartRules.roomStart(film(), request, mayCreate = true, inRoom = true))
        assertNull(RoomStartRules.roomStart(null, request, mayCreate = true, inRoom = false))
        // A recording, a programme's catch-up, a rewind and a channel's archive.
        assertNull(RoomStartRules.roomStart(film("rec:12"), PlaybackRequest("rec:12"), mayCreate = true, inRoom = false))
        assertNull(RoomStartRules.roomStart(channel, PlaybackRequest("ch:s1:7", catchupProgrammeId = "p:1"), mayCreate = true, inRoom = false))
        assertNull(RoomStartRules.roomStart(channel, PlaybackRequest("ch:s1:7", rewindAt = "2026-01-01T20:00:00Z"), mayCreate = true, inRoom = false))
        assertNull(RoomStartRules.roomStart(channel.copy(live = false, archiveStart = "2026-01-01T20:00:00Z"), PlaybackRequest("ch:s1:7"), mayCreate = true, inRoom = false))
        assertNull(RoomStartRules.roomStart(film().copy(mode = PlaybackMode.HANDOFF), request, mayCreate = true, inRoom = false))
    }

    private fun room(clock: RoomClock?, host: Boolean = true, connection: RoomConnection = RoomConnection.Connected) =
        RoomUiState(isHost = host, connection = connection, clock = clock)
    private val untouched = RoomClock(playing = false, positionMs = 0, asOfMs = 1_000)

    @Test fun theHostPutsANewFilmRoomWhereTheTvWasAndKeepsItPlaying() {
        assertEquals(CarryStep.Send(seekToMs = 2_400_000, play = true), CarryOverRules.step(CarryOver(2_400_000, playing = true), room(untouched)))
        // Paused when Watch together was chosen: the room is moved there and waits.
        assertEquals(CarryStep.Send(seekToMs = 2_400_000, play = false), CarryOverRules.step(CarryOver(2_400_000, playing = false), room(untouched)))
        // Near the start there is nothing to seek; playing is still carried.
        assertEquals(CarryStep.Send(seekToMs = null, play = true), CarryOverRules.step(CarryOver(400, playing = true), room(untouched)))
        assertEquals(CarryStep.Done, CarryOverRules.step(CarryOver(null, playing = false), room(untouched)))
    }

    @Test fun itWaitsForTheRoomAndLeavesAloneARoomThatIsNotItsToMove() {
        val carry = CarryOver(2_400_000, playing = true)
        assertEquals(CarryStep.Wait, CarryOverRules.step(carry, room(null)))
        assertEquals(CarryStep.Wait, CarryOverRules.step(carry, room(untouched, connection = RoomConnection.Connecting)))
        assertEquals(CarryStep.Done, CarryOverRules.step(carry, room(untouched, host = false)))
        // Someone already pressed Play or seeked: the room is where they put it.
        assertEquals(CarryStep.Done, CarryOverRules.step(carry, room(untouched.copy(playing = true))))
        assertEquals(CarryStep.Done, CarryOverRules.step(carry, room(untouched.copy(positionMs = 60_000))))
        // A live room plays on by itself and has no position to move to.
        assertEquals(CarryStep.Done, CarryOverRules.step(carry, room(RoomClock(playing = true, positionMs = null, asOfMs = 1_000))))
    }
}
