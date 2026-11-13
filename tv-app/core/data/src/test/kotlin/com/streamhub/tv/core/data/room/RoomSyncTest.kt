package com.streamhub.tv.core.data.room

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RoomSyncTest {
    private val t0 = 1_700_000_000_000L
    private fun playing(positionMs: Long?, asOf: Long = t0) = RoomClock(playing = true, positionMs = positionMs, asOfMs = asOf)
    private fun paused(positionMs: Long?, asOf: Long = t0) = RoomClock(playing = false, positionMs = positionMs, asOfMs = asOf)

    // ---- where the room is ---------------------------------------------------------------------

    @Test fun aPlayingRoomMovesWithTheServerClock() {
        assertEquals(60_000L, RoomSync.positionAt(playing(60_000), t0))
        assertEquals(67_500L, RoomSync.positionAt(playing(60_000), t0 + 7_500))
    }

    @Test fun aPausedRoomStaysWhereItPaused() {
        assertEquals(60_000L, RoomSync.positionAt(paused(60_000), t0 + 3_600_000))
    }

    @Test fun aLiveRoomHasNoPosition() {
        assertNull(RoomSync.positionAt(playing(null), t0 + 5_000))
        assertTrue(playing(null).live)
        assertFalse(playing(0).live)
    }

    // ---- drift threshold -----------------------------------------------------------------------

    @Test fun inStepNeedsNothing() {
        assertEquals(SyncAction.None, RoomSync.decide(playing(60_000), t0 + 5_000, localPositionMs = 65_000, localPlaying = true))
    }

    @Test fun errorsUpToOneSecondAreLeftAlone() {
        for (drift in listOf(-1_000L, -400L, 0L, 400L, 1_000L)) {
            assertEquals("drift $drift", SyncAction.None, RoomSync.decide(playing(60_000), t0, 60_000 + drift, localPlaying = true))
        }
    }

    @Test fun moreThanOneSecondOffSeeksToTheRoomPosition() {
        assertEquals(SyncAction.SeekTo(65_000), RoomSync.decide(playing(60_000), t0 + 5_000, localPositionMs = 63_999, localPlaying = true))
        assertEquals(SyncAction.SeekTo(65_000), RoomSync.decide(playing(60_000), t0 + 5_000, localPositionMs = 66_001, localPlaying = true))
        // Someone else's seek to the start of the film.
        assertEquals(SyncAction.SeekTo(0), RoomSync.decide(playing(0), t0, localPositionMs = 3_600_000, localPlaying = true))
    }

    // ---- pause and play ------------------------------------------------------------------------

    @Test fun aPausedRoomPausesThisPlayerBeforeAnythingElse() {
        // Stop first even when also out of place: the next call lines it up.
        assertEquals(SyncAction.Pause, RoomSync.decide(paused(60_000), t0 + 9_000, localPositionMs = 10_000, localPlaying = true))
        assertEquals(SyncAction.SeekTo(60_000), RoomSync.decide(paused(60_000), t0 + 9_000, localPositionMs = 10_000, localPlaying = false))
        assertEquals(SyncAction.None, RoomSync.decide(paused(60_000), t0 + 9_000, localPositionMs = 60_200, localPlaying = false))
    }

    @Test fun aPlayingRoomStartsAPausedPlayerOnceItIsInPlace() {
        assertEquals(SyncAction.SeekTo(62_000), RoomSync.decide(playing(60_000), t0 + 2_000, localPositionMs = 0, localPlaying = false))
        assertEquals(SyncAction.Play, RoomSync.decide(playing(60_000), t0 + 2_000, localPositionMs = 62_300, localPlaying = false))
    }

    @Test fun theFreshRoomIsPausedAtZero() {
        assertEquals(SyncAction.Pause, RoomSync.decide(paused(0), t0, localPositionMs = 0, localPlaying = true))
        assertEquals(SyncAction.None, RoomSync.decide(paused(0), t0, localPositionMs = 0, localPlaying = false))
    }

    // ---- live rooms ----------------------------------------------------------------------------

    @Test fun aLiveRoomOnlyPlaysAndPauses() {
        // No position to compare with: wherever this player is in its own buffer, it is never told to seek.
        assertEquals(SyncAction.None, RoomSync.decide(playing(null), t0 + 60_000, localPositionMs = 987_654, localPlaying = true))
        assertEquals(SyncAction.Pause, RoomSync.decide(paused(null), t0, localPositionMs = 987_654, localPlaying = true))
        assertEquals(SyncAction.Play, RoomSync.decide(playing(null), t0, localPositionMs = 0, localPlaying = false))
        assertEquals(SyncAction.None, RoomSync.decide(paused(null), t0, localPositionMs = 0, localPlaying = false))
    }

    // ---- stale state ---------------------------------------------------------------------------

    @Test fun aStateFromLongAgoIsStillTheRoomClock() {
        // The server only sends a state when something changes: an hour-old "playing" means an hour has played.
        val hourAgo = playing(120_000, asOf = t0 - 3_600_000)
        assertEquals(3_720_000L, RoomSync.positionAt(hourAgo, t0))
        assertEquals(SyncAction.None, RoomSync.decide(hourAgo, t0, localPositionMs = 3_720_500, localPlaying = true))
        assertEquals(SyncAction.SeekTo(3_720_000), RoomSync.decide(hourAgo, t0, localPositionMs = 120_000, localPlaying = true))
    }

    @Test fun aStateStampedAheadOfOurClockNeverRunsBackwards() {
        // as_of_ms later than our estimate of server time: treat it as "now", not as negative elapsed time.
        val ahead = playing(60_000, asOf = t0 + 4_000)
        assertEquals(60_000L, RoomSync.positionAt(ahead, t0))
        assertEquals(SyncAction.None, RoomSync.decide(ahead, t0, localPositionMs = 60_000, localPlaying = true))
        assertEquals(SyncAction.SeekTo(60_000), RoomSync.decide(ahead, t0, localPositionMs = 55_000, localPlaying = true))
    }

    @Test fun aNegativePositionIsTheStart() {
        assertEquals(0L, RoomSync.positionAt(paused(-50), t0))
    }

    // ---- clock offset --------------------------------------------------------------------------

    @Test fun offsetIsServerTimeMinusTheMiddleOfTheRoundTrip() {
        val clock = ClockOffset()
        assertFalse(clock.synced)
        assertEquals(1_000L, clock.serverNow(1_000))
        // Sent at 1000, back at 1100: the server read its clock at about local 1050.
        clock.onPong(sentAtMs = 1_000, receivedAtMs = 1_100, serverMs = 51_050)
        assertTrue(clock.synced)
        assertEquals(50_000L, clock.offsetMs)
        assertEquals(52_000L, clock.serverNow(2_000))
    }

    @Test fun theSampleWithTheSmallestRoundTripWins() {
        val clock = ClockOffset()
        clock.onPong(1_000, 1_400, 51_300)      // 400 ms round trip, offset 50 100
        clock.onPong(6_000, 6_040, 56_020)      // 40 ms, offset 50 000: better
        assertEquals(50_000L, clock.offsetMs)
        clock.onPong(11_000, 11_900, 61_000)    // 900 ms: a slower sample does not replace it
        assertEquals(50_000L, clock.offsetMs)
        clock.onPong(16_000, 16_040, 66_030)    // as good as the best: the newer one is taken
        assertEquals(50_010L, clock.offsetMs)
    }

    @Test fun aRoundTripThatRanBackwardsIsIgnored() {
        val clock = ClockOffset()
        clock.onPong(5_000, 4_000, 99_000)
        assertFalse(clock.synced)
        assertEquals(0L, clock.offsetMs)
    }

    // ---- reconnect backoff ---------------------------------------------------------------------

    @Test fun backoffIsOneTwoFourEightThenFifteenSeconds() {
        assertEquals(listOf(1_000L, 2_000L, 4_000L, 8_000L, 15_000L, 15_000L, 15_000L), (0..6).map(RoomBackoff::delayMs))
        assertEquals(15_000L, RoomBackoff.delayMs(1_000))
        assertEquals(1_000L, RoomBackoff.delayMs(-1))
    }
}
