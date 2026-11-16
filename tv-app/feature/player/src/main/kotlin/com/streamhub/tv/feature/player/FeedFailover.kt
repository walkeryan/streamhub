package com.streamhub.tv.feature.player

/**
 * Moving on to another feed of a game when the one being watched stops.
 *
 * A game is often carried by several channels at once. When the live channel on screen is one of
 * them and it stops working for longer than [STALL_SWITCH_MS] while the viewer wants it to play,
 * the player switches to the next feed of the same
 * game (GET /playback/alternates). A run of failures visits each feed at most once, in the order
 * the server gives (the feeds after this one, wrapping round); once every feed has been tried the
 * player's own reconnect and error handling take over. A feed that plays steadily for
 * [STEADY_RESET_MS] ends the run. Nothing here ever goes back to a feed because it recovered.
 *
 * "Stops working" means no new video is arriving: neither does the picture move nor does the
 * buffered position grow (or the stream failed). A frozen picture while video still arrives (a
 * hold for the reserve, a slow start) is the feed recovering, not failing, and never counts.
 *
 * Start-up is not a stall: a channel the viewer chose is watched only once it has played. A feed
 * this run switched to gets [STARTUP_GRACE_MS] to start; one that has not played by then failed too.
 *
 * The same rules and numbers as the web player's feedFailover.ts. Times are any monotonic clock in ms.
 */
internal object FeedFailover {
    /** No picture progress and no new video for longer than this, while the viewer wants to play, is a stall. */
    const val STALL_SWITCH_MS = 5_000L
    /** How long a feed switched to automatically has to start before it counts as failed too. */
    const val STARTUP_GRACE_MS = 10_000L
    /** A feed that plays this long without a stall ends the failover run. */
    const val STEADY_RESET_MS = 30_000L

    data class Watch(
        val channel: String,
        val startedAt: Long,
        /** Its picture has moved at least once since it started. */
        val played: Boolean = false,
        /** Since when neither picture nor video has moved while meant to play; null while it plays, receives or is paused. */
        val troubleSince: Long? = null,
        /** Since when it has played without a break. */
        val steadySince: Long? = null,
        /** The failover run: the channels tried in it, the one that first failed first. Empty: no run. */
        val tried: List<String> = emptyList(),
        /** Every feed of this run was tried, or there was none: the player's own recovery is in charge. */
        val exhausted: Boolean = false,
    )

    /** A channel the viewer chose: a fresh watch, no run. */
    fun watchChannel(channel: String, now: Long) = Watch(channel, now)

    /** The feed a switch moved to: the run goes on with it. */
    fun watchSwitched(previous: Watch, channel: String, now: Long): Watch {
        val tried = previous.tried.ifEmpty { listOf(previous.channel) }
        return Watch(channel, now, tried = if (channel in tried) tried else tried + channel)
    }

    /**
     * One look at the picture. [progressing]: it moved since the last look. [arriving]: new video came
     * in since then (the buffered position grew; false once the stream failed). [paused]: not meant to
     * play (paused by the viewer, held for an audio choice, an error no other feed would get past).
     */
    fun observe(watch: Watch, now: Long, progressing: Boolean, paused: Boolean, arriving: Boolean = false): Watch = when {
        progressing -> {
            val steadySince = watch.steadySince ?: now
            // Long enough on one feed: a later stall is a new run, which may visit every feed again.
            val settled = (watch.tried.isNotEmpty() || watch.exhausted) && now - steadySince >= STEADY_RESET_MS
            val next = watch.copy(played = true, troubleSince = null, steadySince = steadySince)
            if (settled) next.copy(tried = emptyList(), exhausted = false) else next
        }
        // Held or starting while video comes in: not trouble, but not play either (neither played nor steady).
        paused || arriving -> watch.copy(troubleSince = null, steadySince = null)
        else -> watch.copy(steadySince = null, troubleSince = watch.troubleSince ?: now)
    }

    /** Is it time to move on to another feed? */
    fun shouldSwitch(watch: Watch, now: Long): Boolean {
        val since = watch.troubleSince ?: return false
        if (watch.exhausted) return false
        // A channel the viewer just chose is starting, not stalling; one this run moved to has a grace to start.
        if (!watch.played) return watch.tried.isNotEmpty() && now - watch.startedAt > STARTUP_GRACE_MS
        return now - since > STALL_SWITCH_MS
    }

    /** The first of [alternates] (channel ids in the server's order) this run has not tried, or null. */
    fun nextFeed(watch: Watch, alternates: List<String>): String? {
        val tried = watch.tried.toSet() + watch.channel
        return alternates.firstOrNull { it !in tried }
    }

    /** Nothing (more) to switch to: the stall is the player's own until a feed plays steadily again. */
    fun giveUp(watch: Watch) = watch.copy(exhausted = true)

    /** The notice shown when the player moved on. */
    fun notice(from: String, to: String) = "$from stopped — switched to $to"
}
