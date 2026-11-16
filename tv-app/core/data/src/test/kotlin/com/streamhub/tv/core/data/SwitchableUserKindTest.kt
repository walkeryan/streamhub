package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.PinKind
import com.streamhub.tv.core.data.model.SwitchableUser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** How "Who's watching?" asks for each person's PIN, from what GET /auth/switchable says of them. */
class SwitchableUserKindTest {
    private fun decode(json: String): SwitchableUser = StreamHubJson.decodeFromString(SwitchableUser.serializer(), json)

    @Test fun theHubSaysWhichKindEachPersonChose() {
        assertEquals(PinKind.Password, decode("""{"id":"u1","has_pin":true,"pin_kind":"password","pin_arrows":null}""").kind)
        assertEquals(PinKind.Arrows, decode("""{"id":"u2","has_pin":true,"pin_kind":"arrows","pin_arrows":6}""").kind)
        assertEquals(PinKind.Pin, decode("""{"id":"u3","has_pin":true,"pin_kind":"pin","pin_arrows":null}""").kind)
        assertNull(decode("""{"id":"u4","has_pin":false,"pin_kind":null}""").kind)
    }

    @Test fun anOlderHubIsReadAsBefore() {
        // No pin_kind at all: an arrow pattern when its length is given, else the keypad.
        assertEquals(PinKind.Arrows, decode("""{"id":"u1","has_pin":true,"pin_arrows":4}""").kind)
        assertEquals(PinKind.Pin, decode("""{"id":"u2","has_pin":true}""").kind)
        assertNull(decode("""{"id":"u3","has_pin":false,"pin_arrows":4}""").kind)
    }

    @Test fun aKindThisAppDoesNotKnowFallsBackToWhatItCanAskFor() {
        assertEquals(PinKind.Arrows, decode("""{"id":"u1","has_pin":true,"pin_kind":"later","pin_arrows":5}""").kind)
        assertEquals(PinKind.Pin, decode("""{"id":"u2","has_pin":true,"pin_kind":"later"}""").kind)
    }
}
