package com.streamhub.tv.shell

import com.streamhub.tv.feature.player.MiniPlayerInfo
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CastReceiverTest {
    private val requestId = "b5769cac-71ff-40ce-9880-18640c074aa0"

    @Test fun loadAcknowledgesTheRequestedCatalogTitleOnlyAfterPlaybackStarts() {
        val load = requireNotNull(parseCastAction("""{"request_id":"$requestId","action":"load","id":"tmdb:movie:1"}"""))
        val media = MiniPlayerInfo("lib:source:1", "Film", null, null, 0, 60000, false, false, false, false)
        CastReceiver.attach("tmdb:movie:2")
        CastReceiver.info = media.copy(playing = true)
        assertFalse(CastReceiver.playingRequestedTitle(load))
        CastReceiver.attach(load.id)
        assertFalse(CastReceiver.playingRequestedTitle(load))
        CastReceiver.info = media
        assertFalse(CastReceiver.playingRequestedTitle(load))
        CastReceiver.info = media.copy(playing = true)
        assertTrue(CastReceiver.playingRequestedTitle(load))
        CastReceiver.attach(null)
    }

    @Test fun invalidRequestsCannotSchedulePlayback() {
        assertNull(parseCastAction("""{"request_id":"bad","action":"load","id":"movie"}"""))
        assertNull(parseCastAction("""{"request_id":"$requestId","action":"seek","position_ms":-1}"""))
        assertNull(parseCastAction("""{"request_id":"$requestId","action":"load"}"""))
    }
}
