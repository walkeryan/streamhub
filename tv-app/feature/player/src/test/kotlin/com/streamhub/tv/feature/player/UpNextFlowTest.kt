package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMarker
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.model.PlaybackRequest
import com.streamhub.tv.core.data.model.UpNext
import com.streamhub.tv.feature.player.UpNextFlow.COUNTDOWN_MS
import com.streamhub.tv.feature.player.UpNextFlow.Effect
import com.streamhub.tv.feature.player.UpNextFlow.Kind
import com.streamhub.tv.feature.player.UpNextFlow.Look
import com.streamhub.tv.feature.player.UpNextFlow.Phase
import com.streamhub.tv.feature.player.UpNextFlow.State
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Skip intro, Skip credits, Up next with its countdown, and Still watching: the same rules as the web player. */
class UpNextFlowTest {
    private val duration = 3_000_000L
    private val intro = PlaybackMarker("intro", 60_000, 120_000)
    private val credits = PlaybackMarker("credits", 2_900_000, 3_000_000)
    private val next = UpNext("lib:p:ep:2", "Episode 2", "S1 · E2")
    private val previous = UpNext("lib:p:ep:0", "Pilot", "S1 · E0")

    private fun episode(markers: List<PlaybackMarker> = listOf(intro, credits), next: UpNext? = this.next, live: Boolean = false, id: String = "lib:p:ep:1") =
        PlaybackDescriptor(id = id, mode = PlaybackMode.DIRECT, live = live, durationMs = duration, markers = markers, next = next, previous = previous)

    private fun plan(d: PlaybackDescriptor = episode(), request: PlaybackRequest? = null, inRoom: Boolean = false) = UpNextFlow.plan(d, request, inRoom)

    /** Looks every 250 ms for [forMs] (the span from [from] to [to] by default), moving while it plays. */
    private fun play(
        plan: UpNextFlow.Plan, from: Long, to: Long, state: State = State(), autoplay: Boolean = true,
        playing: Boolean = true, ended: Boolean = false, clockStart: Long = from, forMs: Long = to - from,
    ): Pair<State, Boolean> {
        var s = state
        var done = false
        var pos = from
        var now = clockStart
        while (now - clockStart <= forMs) {
            val (n, e) = UpNextFlow.observe(s, plan, Look(now, pos, duration, playing, ended), autoplay)
            s = n
            if (e == Effect.CountdownDone) done = true
            if (playing && !ended) pos = (pos + 250).coerceAtMost(to)
            now += 250
        }
        return s to done
    }

    // ---- Which titles get any of this -----------------------------------------------------------

    @Test fun anEpisodeWithANextOneGetsUpNextAndAFilmOnlyEndsPlayback() {
        assertEquals(Kind.Episode, plan().kind)
        assertEquals(Kind.EndsPlayback, plan(episode(next = null)).kind)
        assertEquals(credits, plan().credits)
        assertEquals(previous, plan().previous)
    }

    @Test fun liveRecordingsCatchUpArchiveAndRoomsGetNothing() {
        assertEquals(Kind.None, plan(episode(live = true)).kind)
        assertEquals(Kind.None, plan(episode(id = "rec:42")).kind)
        assertEquals(Kind.None, plan(episode(id = "ch:7")).kind)
        assertEquals(Kind.None, plan(episode(), PlaybackRequest("lib:p:ep:1", catchupProgrammeId = "prog:9")).kind)
        assertEquals(Kind.None, plan(episode(), PlaybackRequest("lib:p:ep:1", rewindAt = "2026-11-18T20:00:00Z")).kind)
        assertEquals(Kind.None, plan(episode().copy(archiveStart = "2026-11-18T19:00:00Z")).kind)
        assertEquals(Kind.None, plan(inRoom = true).kind)
        assertEquals(Kind.None, UpNextFlow.plan(null, null, false).kind)
    }

    @Test fun theLastCreditsMarkerCountsAndANextWithoutAUsableIdIsNone() {
        val early = PlaybackMarker("credits", 100_000, 110_000)
        assertEquals(credits, plan(episode(markers = listOf(credits, early))).credits)
        assertEquals(Kind.EndsPlayback, plan(episode(next = UpNext(id = " "))).kind)
    }

    // ---- Skip intro ------------------------------------------------------------------------------

    @Test fun skipIntroShowsOnlyInsideTheIntroAndSeeksToItsEnd() {
        val p = plan()
        assertNull(UpNextFlow.skipIntroTarget(p, 59_999))
        assertEquals(120_000L, UpNextFlow.skipIntroTarget(p, 60_000))
        assertEquals(120_000L, UpNextFlow.skipIntroTarget(p, 119_999))
        assertNull("it hides itself when the marker ends", UpNextFlow.skipIntroTarget(p, 120_000))
        assertEquals("films skip intros too", 120_000L, UpNextFlow.skipIntroTarget(plan(episode(next = null)), 90_000))
        assertNull(UpNextFlow.skipIntroTarget(plan(episode(live = true)), 90_000))
    }

    // ---- Up next ---------------------------------------------------------------------------------

    @Test fun theCardAppearsAtTheCreditsAndCountsDownTenSecondsThenPlaysNext() {
        val p = plan()
        val (before, _) = play(p, 2_890_000, 2_899_750)
        assertEquals(Phase.Waiting, before.phase)
        assertFalse(UpNextFlow.skipCreditsVisible(p, before, 2_899_750))
        val (shown, doneEarly) = play(p, 2_900_000, 2_905_000, before, clockStart = 10_000_000)
        assertEquals(Phase.Showing, shown.phase)
        assertFalse(doneEarly)
        assertEquals(5, UpNextFlow.secondsLeft(shown))
        assertTrue(UpNextFlow.skipCreditsVisible(p, shown, 2_905_000))
        val (end, done) = play(p, 2_905_250, 2_911_000, shown, clockStart = 10_005_250)
        assertTrue(done)
        assertEquals(Phase.Done, end.phase)
    }

    @Test fun withoutACreditsMarkerTheCardTakesTheLastThirtySecondsAndThereIsNoSkipCredits() {
        val p = plan(episode(markers = emptyList()))
        assertEquals(duration - 30_000, p.cardStartMs(duration))
        val (s, _) = play(p, duration - 30_000, duration - 29_000)
        assertEquals(Phase.Showing, s.phase)
        assertFalse(UpNextFlow.skipCreditsVisible(p, s, duration - 29_000))
    }

    @Test fun theCountdownWaitsWhileThePictureIsPaused() {
        val p = plan()
        val (shown, _) = play(p, 2_900_000, 2_902_000)
        val (paused, done) = play(p, 2_902_000, 2_902_000, shown, playing = false, clockStart = 20_000_000, forMs = 30_000)
        assertFalse(done)
        assertEquals(shown.countedMs, paused.countedMs)
    }

    @Test fun autoplayOffShowsTheCardWithoutEverPlayingByItself() {
        val p = plan()
        val (s, done) = play(p, 2_900_000, 2_999_750, autoplay = false)
        assertFalse(done)
        assertEquals(Phase.Showing, s.phase)
        val (end, endDone) = play(p, duration, duration, s, autoplay = false, ended = true, clockStart = 30_000_000, forMs = 20_000)
        assertFalse(endDone)
        assertTrue(UpNextFlow.cardShowing(end))
    }

    @Test fun keepWatchingHidesTheCardUntilTheVeryEndWhereAFreshCountdownRuns() {
        val p = plan()
        val (shown, _) = play(p, 2_900_000, 2_906_000)
        val kept = UpNextFlow.dismiss(shown)
        assertEquals(Phase.Dismissed, kept.phase)
        assertFalse("skip credits goes with the card", UpNextFlow.skipCreditsVisible(p, kept, 2_906_250))
        val (still, noAuto) = play(p, 2_906_250, 2_999_750, kept)
        assertEquals(Phase.Dismissed, still.phase)
        assertFalse(noAuto)
        val atEnd = UpNextFlow.observe(still, p, Look(40_000_000, duration, duration, playing = false, ended = true), true).first
        assertEquals(Phase.AtEnd, atEnd.phase)
        assertEquals(10, UpNextFlow.secondsLeft(atEnd))
        val (_, done) = play(p, duration, duration, atEnd, ended = true, playing = false, clockStart = 40_000_250, forMs = 11_000)
        assertTrue("the countdown runs at the end, where nothing plays", done)
    }

    @Test fun seekingBackBeforeTheCreditsArmsTheCardAgain() {
        val p = plan()
        val (shown, _) = play(p, 2_900_000, 2_901_000)
        val kept = UpNextFlow.dismiss(shown)
        val back = UpNextFlow.observe(kept, p, Look(50_000_000, 1_000_000, duration, true, false), true).first
        assertEquals(Phase.Waiting, back.phase)
        val again = UpNextFlow.observe(back, p, Look(50_000_250, 2_900_100, duration, true, false), true).first
        assertEquals(Phase.Showing, again.phase)
        assertEquals(0L, again.countedMs)
    }

    @Test fun dismissOnlyActsOnAVisibleCard() {
        assertEquals(State(), UpNextFlow.dismiss(State()))
        val done = State(phase = Phase.Done)
        assertEquals(done, UpNextFlow.dismiss(done))
    }

    @Test fun aLongGapBetweenLooksDoesNotEatTheCountdown() {
        val p = plan()
        val (shown, _) = play(p, 2_900_000, 2_900_000)
        val (later, effect) = UpNextFlow.observe(shown, p, Look(shown.lastLookMs!! + 60_000, 2_900_250, duration, true, false), true)
        assertEquals(Effect.None, effect)
        assertTrue(later.countedMs <= 1_000)
    }

    @Test fun endedWithoutAKnownLengthStillOffersTheNextEpisode() {
        val p = plan(episode(markers = emptyList()))
        assertNull(p.cardStartMs(0))
        val s = UpNextFlow.observe(State(), p, Look(0, 0, 0, playing = false, ended = true), true).first
        assertEquals(Phase.AtEnd, s.phase)
    }

    // ---- Films -----------------------------------------------------------------------------------

    @Test fun aFilmShowsSkipCreditsInsideItsCreditsAndNeverACard() {
        val p = plan(episode(next = null))
        val (s, done) = play(p, 2_899_000, 2_999_000)
        assertFalse(done)
        assertEquals(Phase.Waiting, s.phase)
        assertTrue(UpNextFlow.skipCreditsVisible(p, s, 2_950_000))
        assertFalse(UpNextFlow.skipCreditsVisible(p, s, 2_899_000))
        assertNull(p.cardStartMs(duration))
    }

    // ---- Completion and Still watching -----------------------------------------------------------

    @Test fun movingOnFromTheCreditsOrTheEndReportsTheEpisodeWatchedButNotFromTheMiddle() {
        val p = plan()
        assertTrue(UpNextFlow.finishesCurrent(p, 2_900_000, duration, ended = false))
        assertTrue(UpNextFlow.finishesCurrent(p, 100, duration, ended = true))
        assertFalse(UpNextFlow.finishesCurrent(p, 1_000_000, duration, ended = false))
        val noMarkers = plan(episode(markers = emptyList()))
        assertTrue(UpNextFlow.finishesCurrent(noMarkers, duration - 20_000, duration, ended = false))
        assertFalse(UpNextFlow.finishesCurrent(noMarkers, duration - 40_000, duration, ended = false))
    }

    @Test fun stillWatchingIsAskedInsteadOfAFourthUnattendedAutoplay() {
        assertFalse(UpNextFlow.asksStillWatching(0))
        assertFalse(UpNextFlow.asksStillWatching(2))
        assertTrue(UpNextFlow.asksStillWatching(3))
    }

    @Test fun theCountdownIsAlwaysTenSeconds() = assertEquals(10_000L, COUNTDOWN_MS)
}
