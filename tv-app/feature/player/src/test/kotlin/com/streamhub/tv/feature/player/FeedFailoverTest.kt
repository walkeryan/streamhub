package com.streamhub.tv.feature.player

import com.streamhub.tv.feature.player.FeedFailover.STALL_SWITCH_MS
import com.streamhub.tv.feature.player.FeedFailover.STARTUP_GRACE_MS
import com.streamhub.tv.feature.player.FeedFailover.STEADY_RESET_MS
import com.streamhub.tv.feature.player.FeedFailover.Watch
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The auto tuner's rules: when a stalled game feed moves on, and where to. */
class FeedFailoverTest {
    /** One look a second from [from] to [to] (exclusive). */
    private fun Watch.run(from: Long, to: Long, progressing: Boolean, paused: Boolean = false): Watch {
        var w = this
        var t = from
        while (t < to) { w = FeedFailover.observe(w, t, progressing, paused); t += 1_000 }
        return w
    }

    private fun playing(channel: String = "ch:a") = FeedFailover.watchChannel(channel, 0).run(0, 3_000, progressing = true)

    @Test fun aChannelThatPlayedAndThenStopsForLongerThanTheWindowSwitches() {
        val w = FeedFailover.observe(playing(), 20_000, progressing = false, paused = false)
        assertFalse(FeedFailover.shouldSwitch(w, 20_000 + STALL_SWITCH_MS))
        assertTrue(FeedFailover.shouldSwitch(w, 20_000 + STALL_SWITCH_MS + 1))
    }

    @Test fun startUpOfAChannelTheViewerChoseIsNeverAStall() {
        val w = FeedFailover.watchChannel("ch:a", 0).run(0, 60_000, progressing = false)
        assertFalse(FeedFailover.shouldSwitch(w, 60_000))
    }

    @Test fun aPauseByTheViewerIsNotAStall() {
        val w = playing().run(3_000, 60_000, progressing = false, paused = true)
        assertNull(w.troubleSince)
        assertFalse(FeedFailover.shouldSwitch(w, 60_000))
    }

    @Test fun aShortGapThatRecoversResetsTheClock() {
        var w = playing().run(3_000, 7_000, progressing = false)
        w = FeedFailover.observe(w, 7_000, progressing = true, paused = false)
        w = FeedFailover.observe(w, 8_000, progressing = false, paused = false)
        assertFalse(FeedFailover.shouldSwitch(w, 12_000))
        assertTrue(FeedFailover.shouldSwitch(w, 13_001))
    }

    @Test fun theNextFeedSkipsTheCurrentOneAndEveryFeedThisRunTried() {
        var w = playing("ch:b")
        assertEquals("ch:c", FeedFailover.nextFeed(w, listOf("ch:c", "ch:d", "ch:a")))
        w = FeedFailover.watchSwitched(w, "ch:c", 10_000)
        assertEquals(listOf("ch:b", "ch:c"), w.tried)
        assertEquals("ch:d", FeedFailover.nextFeed(w, listOf("ch:d", "ch:a", "ch:b")))
        w = FeedFailover.watchSwitched(w, "ch:d", 20_000)
        w = FeedFailover.watchSwitched(w, "ch:a", 30_000)
        assertNull("each feed at most once per run", FeedFailover.nextFeed(w, listOf("ch:b", "ch:c", "ch:d")))
        assertNull(FeedFailover.nextFeed(w, emptyList()))
    }

    @Test fun aFeedSwitchedToHasAGraceToStartAfterWhichNotStartingIsAFailure() {
        val w = FeedFailover.watchSwitched(playing(), "ch:b", 10_000).run(10_000, 10_000 + STARTUP_GRACE_MS + 1_000, progressing = false)
        assertFalse(FeedFailover.shouldSwitch(w, 10_000 + STARTUP_GRACE_MS))
        assertTrue(FeedFailover.shouldSwitch(w, 10_000 + STARTUP_GRACE_MS + 1))
    }

    @Test fun aRunEndsOnceAFeedHasPlayedSteadily() {
        var w = FeedFailover.watchSwitched(playing(), "ch:b", 10_000)
        w = w.run(10_000, 10_000 + STEADY_RESET_MS - 1_000, progressing = true)
        assertEquals(listOf("ch:a", "ch:b"), w.tried)
        w = w.run(10_000 + STEADY_RESET_MS - 1_000, 10_000 + STEADY_RESET_MS + 2_000, progressing = true)
        assertTrue(w.tried.isEmpty())
        assertEquals("ch:c", FeedFailover.nextFeed(w, listOf("ch:c", "ch:a")))
    }

    @Test fun givingUpHandsTheStallToThePlayerUntilAFeedPlaysSteadily() {
        var w = playing().run(3_000, 10_000, progressing = false)
        assertTrue(FeedFailover.shouldSwitch(w, 10_000))
        w = FeedFailover.giveUp(w)
        assertFalse(FeedFailover.shouldSwitch(w, 60_000))
        w = w.run(60_000, 60_000 + STEADY_RESET_MS + 1_000, progressing = true)
        assertFalse(w.exhausted)
        w = w.run(100_000, 110_000, progressing = false)
        assertTrue(FeedFailover.shouldSwitch(w, 110_000))
    }

    @Test fun theNoticeNamesBothChannels() {
        assertEquals("Harbor Sports 1 stopped — switched to Ridge Sports", FeedFailover.notice("Harbor Sports 1", "Ridge Sports"))
    }
}
