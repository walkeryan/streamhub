package com.streamhub.tv.feature.player

import android.view.KeyEvent
import com.streamhub.tv.core.data.model.BufferWindow
import java.time.Instant
import org.junit.Assert.*
import org.junit.Test

class WatchedTimelineTest {
    private val start = Instant.parse("2026-10-14T08:00:00Z")
    private fun window(from: Instant, to: Instant) =
        BufferWindow(start = from.toString(), end = to.toString())

    @Test fun longTuneMovesAcrossMidnightInBothDirectionsAndStopsAtItsEdges() {
        val end = start.plusSeconds(19 * 3600)
        val range = WatchedTimeline.from(window(start, end))!!
        val cursor = range.move(end, -PlayerKeyMap.SEEK_STEP_MS)
        assertEquals(end.minusSeconds(10), range.seekAt(cursor))
        assertEquals(start, range.seekAt(range.move(cursor, -24 * 3600000L)))
        assertNull(range.seekAt(range.move(cursor, 30000)))
        assertEquals(0.5f, range.progress(start.plusSeconds(19 * 1800)), .0001f)
    }

    @Test fun advancingWindowPreservesAnAbsoluteSelectionAndANewTuneCannotSeekToOldHistory() {
        val selected = start.plusSeconds(20)
        val grown = WatchedTimeline.from(window(start, start.plusSeconds(150)))!!
        assertEquals(selected, grown.seekAt(selected))
        val trimmed = WatchedTimeline.from(window(start.plusSeconds(35), start.plusSeconds(190)))!!
        assertEquals(trimmed.start, trimmed.seekAt(selected))
        assertNull(WatchedTimeline.from(BufferWindow()))
        val tuned = WatchedTimeline.from(window(start.plusSeconds(200), start.plusSeconds(206)))!!
        assertEquals(tuned.start, tuned.move(selected, -10000))
        assertNull(tuned.seekAt(tuned.end))
    }

    @Test fun shortMediaWindowOnlyOffersTimeActuallyPlayedSinceTheLastTune() {
        assertNull(localTuneWindow(5000, 60000, 0))
        assertNull(localTuneWindow(5000, -1, 3000))
        val range = localTuneWindow(5000, 60000, 3000)!!
        assertEquals(3000, range.durationMs)
        assertEquals(Instant.ofEpochMilli(62000), range.start)
        assertEquals(Instant.ofEpochMilli(65000), range.end)
        assertEquals(60000, localTuneWindow(10000, 60000, 180000)!!.durationMs)
    }

    @Test fun optionalBufferFailureCanKeepTheGrantedStreamButPermissionAndStreamLimitsCannot() {
        fun failure(status: Int, code: String) = com.streamhub.tv.core.data.api.ApiException(status,
            com.streamhub.tv.core.data.model.ApiError(code = code), "fixture")
        assertTrue(optionalBufferUnavailable(failure(409, "buffer_storage_full")))
        assertTrue(optionalBufferUnavailable(failure(409, "buffer_session_active")))
        assertFalse(optionalBufferUnavailable(failure(403, "buffer_storage_full")))
        assertFalse(optionalBufferUnavailable(failure(401, "session_expired")))
        assertFalse(optionalBufferUnavailable(failure(409, "user_stream_limit")))
        assertFalse(optionalBufferUnavailable(failure(409, "source_busy")))
    }

    @Test fun physicalRewindOpensGrantedChannelHistoryAndKeepsRoomMediaCommandsScoped() {
        fun rewind(granted: Boolean, inRoom: Boolean) = PlayerKeyMap.resolve(
            KeyEvent.KEYCODE_MEDIA_REWIND, isLive = true, menuOpen = false, controlsVisible = false,
            inRoom = inRoom, watchedBuffer = granted)
        assertEquals(PlayerCommand.OpenArchive, rewind(true, false))
        assertEquals(PlayerCommand.Seek(-30000), rewind(false, false))
        assertEquals(PlayerCommand.Seek(-30000), rewind(true, true))
    }
}
