package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMarker
import com.streamhub.tv.core.data.model.PlaybackRequest
import com.streamhub.tv.core.data.model.UpNext

/**
 * Skip intro, Skip credits, Up next and Still watching (design doc "Behaviour", the same rules as
 * the web player). Pure: the player feeds it what it sees about four times a second and acts on
 * what it answers, so every rule is unit-tested without a device.
 *
 *  - Skip intro shows while the position is inside an intro marker and seeks to its end.
 *  - For an episode with a next one, the Up next card (and Skip credits) appear at the start of the
 *    last credits marker, or in the last [NO_MARKER_LEAD_MS] without one. A [COUNTDOWN_MS] countdown
 *    runs while the picture plays; at zero the next episode starts. With autoplay off the card waits
 *    for "Play next" instead.
 *  - Back, "Keep watching" or any seek dismisses the card; the episode plays to its end, where the
 *    card returns with a fresh countdown. Seeking back before the credits arms it again.
 *  - A film (or the last episode) with a credits marker gets Skip credits, which ends playback.
 *  - Live, recordings, catch-up, archive and watch rooms get none of this.
 */
object UpNextFlow {
    const val COUNTDOWN_MS = 10_000L
    /** Without a credits marker the card shows this long before the end. */
    const val NO_MARKER_LEAD_MS = 30_000L
    /** A position this far before the card's start counts as "seeked back": the card is armed again. */
    const val REARM_SLACK_MS = 1_000L
    /** Episodes started by autoplay in a row, with no key press between, before "Still watching?". */
    const val MAX_UNATTENDED_AUTOPLAYS = 3
    /** A gap between two looks longer than this (the app was asleep) counts as this much. */
    private const val MAX_STEP_MS = 1_000L

    enum class Kind {
        /** Nothing to skip or play next: live, recordings, catch-up, rooms, or a title without the fields. */
        None,
        /** An episode with a next one: Up next, Skip credits plays it. */
        Episode,
        /** A film, or an episode without a next one: Skip credits (when marked) ends playback. */
        EndsPlayback,
    }

    /** What this title allows, fixed for one descriptor. */
    data class Plan(
        val kind: Kind,
        val intros: List<PlaybackMarker> = emptyList(),
        /** The last credits marker. */
        val credits: PlaybackMarker? = null,
        val next: UpNext? = null,
        val previous: UpNext? = null,
    ) {
        /** Where the Up next card first shows; null when this title has no card (or no known length yet). */
        fun cardStartMs(durationMs: Long): Long? = when {
            kind != Kind.Episode -> null
            credits != null -> credits.startMs
            durationMs > NO_MARKER_LEAD_MS -> durationMs - NO_MARKER_LEAD_MS
            else -> null
        }
    }

    val NONE = Plan(Kind.None)

    /**
     * The plan for [d]. A title gets the episode features only when it is one the viewer chose to
     * watch on its own: not live, not a recording, catch-up or archive (by [request] or by the
     * descriptor), not in a watch room.
     */
    fun plan(d: PlaybackDescriptor?, request: PlaybackRequest?, inRoom: Boolean): Plan {
        if (d == null || inRoom || d.live || d.bufferAvailable || d.archiveStart != null || d.startPositionMs != null) return NONE
        if (d.id.startsWith("rec:") || d.id.startsWith("ch:") || d.id.startsWith("prog:")) return NONE
        if (request != null && (request.catchupProgrammeId != null || request.rewindAt != null)) return NONE
        val markers = d.markers.filter { it.valid }
        val next = d.next?.takeIf(::playable)
        return Plan(
            kind = if (next != null) Kind.Episode else Kind.EndsPlayback,
            intros = markers.filter { it.type == PlaybackMarker.INTRO },
            credits = markers.filter { it.type == PlaybackMarker.CREDITS }.maxByOrNull { it.startMs },
            next = next,
            previous = d.previous?.takeIf(::playable),
        )
    }

    private fun playable(u: UpNext): Boolean = u.id.isNotBlank() && u.id.length <= 512 && u.id.all { it in '!'..'~' }

    enum class Phase {
        /** Before the card's start. */
        Waiting,
        /** The card is up (from the credits or the last 30 s). */
        Showing,
        /** Kept watching: hidden until the very end (or until seeked back before the credits). */
        Dismissed,
        /** The card is up at the very end, with a fresh countdown. */
        AtEnd,
        /** The countdown ran out: the next episode is being started (or Still watching asked). */
        Done,
    }

    data class State(
        val phase: Phase = Phase.Waiting,
        /** Countdown time elapsed while it was allowed to run. */
        val countedMs: Long = 0,
        val lastLookMs: Long? = null,
    )

    /** One look at the player. [positionMs] is the title position (the scrub target while seeking). */
    data class Look(val nowMs: Long, val positionMs: Long, val durationMs: Long, val playing: Boolean, val ended: Boolean)

    /** What the player should do after a look. */
    enum class Effect { None, CountdownDone }

    /** The next state after [look]; [autoplay] is the profile's `autoplay_next`. */
    fun observe(state: State, plan: Plan, look: Look, autoplay: Boolean): Pair<State, Effect> {
        val step = state.lastLookMs?.let { (look.nowMs - it).coerceIn(0, MAX_STEP_MS) } ?: 0L
        val start = plan.cardStartMs(look.durationMs)
        var s = state.copy(lastLookMs = look.nowMs)
        if (plan.kind != Kind.Episode) return s to Effect.None
        val before = start == null || look.positionMs < start - REARM_SLACK_MS
        val wasUp = cardShowing(state)
        s = when (s.phase) {
            Phase.Waiting -> when {
                look.ended -> s.copy(phase = Phase.AtEnd, countedMs = 0)
                start != null && look.positionMs >= start -> s.copy(phase = Phase.Showing, countedMs = 0)
                else -> s
            }
            Phase.Showing -> if (before && !look.ended) s.copy(phase = Phase.Waiting, countedMs = 0) else s
            Phase.Dismissed -> when {
                look.ended -> s.copy(phase = Phase.AtEnd, countedMs = 0)
                before -> s.copy(phase = Phase.Waiting, countedMs = 0)
                else -> s
            }
            Phase.AtEnd -> if (!look.ended && before) s.copy(phase = Phase.Waiting, countedMs = 0) else s
            Phase.Done -> s
        }
        // The countdown runs while the picture plays, and at the end (where nothing plays any more). It
        // starts at the look after the card appeared (or restarted at the end), not with the gap before it.
        if (autoplay && wasUp && s.phase == state.phase && cardShowing(s) && (look.playing || look.ended)) {
            val counted = s.countedMs + step
            if (counted >= COUNTDOWN_MS) return s.copy(phase = Phase.Done, countedMs = COUNTDOWN_MS) to Effect.CountdownDone
            s = s.copy(countedMs = counted)
        }
        return s to Effect.None
    }

    /** Back, "Keep watching" or a seek while the card is up. */
    fun dismiss(state: State): State = if (cardShowing(state)) state.copy(phase = Phase.Dismissed, countedMs = 0) else state

    fun cardShowing(state: State): Boolean = state.phase == Phase.Showing || state.phase == Phase.AtEnd

    /** Whole seconds left on the countdown, as the ring shows them. */
    fun secondsLeft(state: State): Int = ((COUNTDOWN_MS - state.countedMs).coerceAtLeast(0) + 999).div(1000).toInt()

    /** Where Skip intro seeks to: the end of the intro marker the position is in; null outside one. */
    fun skipIntroTarget(plan: Plan, positionMs: Long): Long? =
        if (plan.kind == Kind.None) null else plan.intros.firstOrNull { positionMs >= it.startMs && positionMs < it.endMs }?.endMs

    /** Whether Skip credits shows: inside the credits marker, for an episode while its card is up, for a film always. */
    fun skipCreditsVisible(plan: Plan, state: State, positionMs: Long): Boolean {
        val c = plan.credits ?: return false
        val inside = positionMs >= c.startMs && positionMs < c.endMs
        return when (plan.kind) {
            Kind.None -> false
            Kind.Episode -> inside && cardShowing(state)
            Kind.EndsPlayback -> inside
        }
    }

    /**
     * Whether moving on from this episode reports it watched: only from the credits / last 30 s or
     * the end, not when "next episode" is pressed in the middle of it.
     */
    fun finishesCurrent(plan: Plan, positionMs: Long, durationMs: Long, ended: Boolean): Boolean {
        if (ended) return true
        val start = plan.credits?.startMs ?: (durationMs - NO_MARKER_LEAD_MS).takeIf { durationMs > NO_MARKER_LEAD_MS } ?: return false
        return positionMs >= start
    }

    /** Still watching? is asked instead of starting another episode after this many autoplays in a row. */
    fun asksStillWatching(unattendedAutoplays: Int): Boolean = unattendedAutoplays >= MAX_UNATTENDED_AUTOPLAYS
}
