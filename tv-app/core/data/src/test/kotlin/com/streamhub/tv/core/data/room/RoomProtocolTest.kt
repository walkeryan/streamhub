package com.streamhub.tv.core.data.room

import android.view.KeyEvent
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.Room
import com.streamhub.tv.core.data.model.RoomJoinRequest
import com.streamhub.tv.core.data.model.RoomJoined
import com.streamhub.tv.core.data.model.RoomMember
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RoomProtocolTest {
    @Test fun clientMessagesMatchTheContract() {
        assertEquals("""{"type":"ping","t":1234}""", RoomProtocol.ping(1234))
        assertEquals("""{"type":"play"}""", RoomProtocol.play())
        assertEquals("""{"type":"pause"}""", RoomProtocol.pause())
        assertEquals("""{"type":"seek","position_ms":90000}""", RoomProtocol.seek(90_000))
        assertEquals("""{"type":"seek","position_ms":0}""", RoomProtocol.seek(-5))
        assertEquals("""{"type":"buffering","on":true}""", RoomProtocol.buffering(true))
        assertEquals("""{"type":"lock","on":false}""", RoomProtocol.lock(false))
    }

    @Test fun decodesEveryServerMessage() {
        assertEquals(RoomMessage.Pong(1234, 99_000), RoomProtocol.decode("""{"type":"pong","t":1234,"server_ms":99000}"""))
        assertEquals(
            RoomMessage.State(RoomClock(true, 60_000, 99_000), by = "m2", controlsLocked = true),
            RoomProtocol.decode("""{"type":"state","playing":true,"position_ms":60000,"as_of_ms":99000,"by":"m2","controls_locked":true}"""),
        )
        assertEquals(
            RoomMessage.Members(listOf(RoomMember("m1", "Alex", host = true, buffering = false), RoomMember("m2", "Sam", host = false, buffering = true))),
            RoomProtocol.decode("""{"type":"members","members":[{"id":"m1","name":"Alex","host":true,"buffering":false},{"id":"m2","name":"Sam","host":false,"buffering":true,"new":1}]}"""),
        )
        assertEquals(RoomMessage.Waiting("Sam"), RoomProtocol.decode("""{"type":"waiting","name":"Sam"}"""))
        assertEquals(RoomMessage.Error("controls_locked", "Only the host"), RoomProtocol.decode("""{"type":"error","code":"controls_locked","message":"Only the host"}"""))
        assertEquals(RoomMessage.Closed("deleted"), RoomProtocol.decode("""{"type":"closed","reason":"deleted"}"""))
    }

    @Test fun aLiveRoomStateHasNoPositionAndTheRoomCanBeTheCause() {
        val m = RoomProtocol.decode("""{"type":"state","playing":true,"position_ms":null,"as_of_ms":99000,"by":null,"controls_locked":false}""") as RoomMessage.State
        assertNull(m.clock.positionMs)
        assertTrue(m.clock.live)
        assertNull(m.by)
        assertFalse(m.controlsLocked)
    }

    @Test fun aSharedStreamRoomsStateCarriesItsEpochAndTimeZero() {
        val m = RoomProtocol.decode("""{"type":"state","playing":true,"position_ms":3602000,"as_of_ms":99000,"by":"m1","controls_locked":false,"stream_epoch":2,"stream_start_ms":3600000}""") as RoomMessage.State
        assertEquals(RoomStream(2, 3_600_000), m.stream)
        assertEquals(3_602_000L, m.clock.positionMs)
        // Other rooms send neither field.
        val own = RoomProtocol.decode("""{"type":"state","playing":true,"position_ms":3602000,"as_of_ms":99000,"by":"m1","controls_locked":false}""") as RoomMessage.State
        assertNull(own.stream)
        // Half of the pair positions nothing, and a null epoch is no epoch.
        for (half in listOf(""""stream_epoch":2""", """"stream_start_ms":5000""", """"stream_epoch":null,"stream_start_ms":5000""")) {
            assertNull(half, (RoomProtocol.decode("""{"type":"state","playing":false,"position_ms":0,"as_of_ms":1,$half}""") as RoomMessage.State).stream)
        }
        // What the tests' own encoder writes reads back the same.
        val clock = RoomClock(false, 600_000, 5)
        assertEquals(RoomMessage.State(clock, null, false, RoomStream(7, 600_000)), RoomProtocol.decode(RoomProtocol.stateJson(clock, stream = RoomStream(7, 600_000)).toString()))
    }

    @Test fun onlyALostSeatOrConnectionIsJoinedAgain() {
        for (reason in listOf("silent", "slow", "unauthorized", "not_a_member", ROOM_CONNECTION_LOST)) assertTrue(reason, roomSeatRecoverable(reason))
        // The room is over, or another connection took this seat on purpose.
        for (reason in listOf("deleted", "expired", "shutdown", "not_found", "replaced", "closed", "something_new", null)) assertFalse("$reason", roomSeatRecoverable(reason))
        // The descriptor call's refusals a join with the member token can mend.
        assertTrue(roomSeatLost("room_unauthorized"))
        assertTrue(roomSeatLost("not_a_member"))
        for (code in listOf("room_not_found", "session_revoked", "user_suspended", "content_restricted", "user_stream_limit", null)) assertFalse("$code", roomSeatLost(code))
    }

    @Test fun aRejoinNamesTheSeatItHoldsAndAFirstJoinDoesNot() {
        val first = StreamHubJson.encodeToString(RoomJoinRequest.serializer(), RoomJoinRequest())
        assertEquals("""{"client":"android_tv","allow_unverified_audio":false}""", first)
        val again = RoomJoinRequest(allowUnverifiedAudio = true, memberToken = "seat-token")
        assertEquals("""{"client":"android_tv","allow_unverified_audio":true,"member_token":"seat-token"}""", StreamHubJson.encodeToString(RoomJoinRequest.serializer(), again))
        assertFalse(again.toString().contains("seat-token"))
    }

    @Test fun whatIsNotUnderstoodIsIgnored() {
        for (text in listOf("", "not json", "[1,2]", "{}", """{"type":"chat","text":"hi"}""", """{"type":"pong"}""", """{"type":"state","playing":true}""", """{"type":"members","members":"x"}""")) {
            assertEquals(text, RoomMessage.Unknown, RoomProtocol.decode(text))
        }
    }

    @Test fun everyCloseReasonHasItsOwnExplanation() {
        val reasons = listOf("expired", "deleted", "shutdown", "silent", "replaced", "slow", "unauthorized", "not_found", "not_a_member", ROOM_CONNECTION_LOST)
        for (reason in reasons) {
            val e = roomClosedMessage(reason)
            assertTrue(reason, e.title.isNotBlank() && e.body.isNotBlank())
            assertFalse(reason, e.canRetry)
        }
        assertEquals("The host ended the room", roomClosedMessage("deleted").title)
        assertEquals("The room has ended", roomClosedMessage("something_new").title)
        assertEquals("The room has ended", roomClosedMessage(null).title)
    }

    @Test fun refusalsTheViewerCanActOnAreWorded() {
        assertEquals("Only the host can control playback", roomRefusalText("controls_locked"))
        assertEquals("Only the host can do that", roomRefusalText("host_only"))
        assertTrue(roomRefusalText("live_no_seek")!!.contains("live"))
        assertNull(roomRefusalText("bad_message"))
    }

    // ---- codes and addresses -------------------------------------------------------------------

    @Test fun codeEntryTakesSixCharactersOfTheAlphabetInEitherCase() {
        var entry = ""
        for (c in "ab2cd9xyz") entry = RoomCode.append(entry, c)
        assertEquals("AB2CD9", entry)
        assertTrue(RoomCode.isComplete(entry))
        assertEquals("AB2CD", RoomCode.backspace(entry))
        assertFalse(RoomCode.isComplete("AB2CD"))
        assertEquals("", RoomCode.backspace(""))
    }

    @Test fun lookAlikesAndPunctuationAreNotInAnyCode() {
        for (c in "IO01 -_.") assertEquals("ABC", RoomCode.append("ABC", c))
        assertEquals("ABC234", RoomCode.normalize(" abc-234 "))
        assertEquals("ABCDEF", RoomCode.normalize("abcdefgh"))
        assertEquals(32, RoomCode.ALPHABET.length)
        assertEquals("ABC 234", RoomCode.display("ABC234"))
    }

    @Test fun remoteDigitsAndKeyboardLettersTypeCodeCharacters() {
        assertEquals('2', RoomCode.charOf(KeyEvent.KEYCODE_2))
        assertEquals('9', RoomCode.charOf(KeyEvent.KEYCODE_NUMPAD_9))
        assertEquals('A', RoomCode.charOf(KeyEvent.KEYCODE_A))
        assertEquals('Z', RoomCode.charOf(KeyEvent.KEYCODE_Z))
        for (key in listOf(KeyEvent.KEYCODE_0, KeyEvent.KEYCODE_1, KeyEvent.KEYCODE_I, KeyEvent.KEYCODE_O, KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_ENTER)) {
            assertNull(RoomCode.charOf(key))
        }
    }

    @Test fun shareAndSocketAddressesComeFromThePairedServer() {
        assertEquals("http://hub.test:8787/room/ABC234", roomShareUrl("hub.test", "ABC234"))
        assertEquals("https://hub.test/room/ABC234", roomShareUrl("https://hub.test/", "ABC234", joinPath = "/room/ABC234"))
        // A join path that is not a path on this server is not trusted.
        assertEquals("http://hub.test:8787/room/ABC234", roomShareUrl("hub.test:8787", "ABC234", joinPath = "//elsewhere.test/x"))
        assertEquals("ws://hub.test:8787/api/v1/rooms/ABC234/ws?member_token=tok.en-1", roomSocketUrl("http://hub.test:8787", "/api/v1/rooms/ABC234/ws", "tok.en-1"))
        assertEquals("wss://hub.test/api/v1/rooms/ABC234/ws?member_token=a%2Bb%3D", roomSocketUrl("https://hub.test", "api/v1/rooms/ABC234/ws", "a+b="))
    }

    @Test fun roomAndSeatDecodeFromTheWire() {
        val room = StreamHubJson.decodeFromString(Room.serializer(), """
            {"code":"ABC234","join_path":"/room/ABC234","id":"tmdb:movie:603","live":false,"variant_id":null,"audio_track":null,
             "host_name":"Alex","is_host":true,"controls_locked":false,"waiting_for":null,"max_members":8,
             "members":[{"id":"m1","name":"Alex","host":true,"buffering":false}],
             "state":{"playing":false,"position_ms":0,"as_of_ms":1700000000000},"server_ms":1700000000100,
             "title":{"id":"tmdb:movie:603","kind":"movie","title":"The Long Film","year":1999,"poster":null}}
        """.trimIndent())
        assertEquals("ABC234", room.code)
        assertTrue(room.isHost)
        assertEquals(0L, room.state.positionMs)
        assertEquals("The Long Film", room.title.title)
        val live = StreamHubJson.decodeFromString(Room.serializer(), """{"code":"XYZ789","live":true,"state":{"playing":true,"position_ms":null,"as_of_ms":5},"title":{"kind":"something_new"}}""")
        assertNull(live.state.positionMs)
        assertEquals("something_new", live.title.kind)
        val seat = StreamHubJson.decodeFromString(RoomJoined.serializer(), """
            {"member_token":"secret-token","member_id":"m2","expires_in_seconds":7200,"ws_path":"/api/v1/rooms/ABC234/ws",
             "descriptor":{"id":"tmdb:movie:603","mode":"hls","url":"http://hub.test:8787/api/v1/stream/x/index.m3u8","title":"The Long Film"}}
        """.trimIndent())
        assertEquals("m2", seat.memberId)
        assertFalse(seat.toString().contains("secret-token"))
    }
}
