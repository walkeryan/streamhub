package com.streamhub.tv.feature.player

import com.streamhub.tv.core.player.PlayerUiState

/**
 * What the web app's mini player bar shows while the native player is in picture-in-picture (the
 * shell sends it as the `mini_player` bridge event). Plain values, so the mapping is unit-tested.
 * Positions are whole seconds: the bar shows seconds, and the event then changes about once a second.
 */
data class MiniPlayerInfo(
    val id: String,
    val title: String,
    val subtitle: String?,
    val image: String?,
    val positionMs: Long,
    val durationMs: Long?,
    val playing: Boolean,
    val hasNext: Boolean,
    val hasPrevious: Boolean,
    val live: Boolean,
)

/** What the bar can ask of the native player (the `mini_player_command` bridge message). */
sealed interface MiniPlayerCommand {
    data object PlayPause : MiniPlayerCommand
    data class SeekBy(val deltaMs: Long) : MiniPlayerCommand
    data object Next : MiniPlayerCommand
    data object Previous : MiniPlayerCommand
    /** Back to full screen. */
    data object Expand : MiniPlayerCommand
    /** Stop playback and close the player. */
    data object Close : MiniPlayerCommand
}

/**
 * The bar's view of [s] and [ps]; null while nothing is loaded. An episode reads as its show over
 * "S2 · E3 · Episode name"; anything else as its own title and subtitle. Live has no position.
 * [wantsToPlay]: the viewer has not paused it (a stall while buffering still reads as playing).
 */
fun miniPlayerInfo(s: PlayerScreenState, ps: PlayerUiState, wantsToPlay: Boolean): MiniPlayerInfo? {
    val d = s.descriptor ?: return null
    val live = d.live && !s.bufferTimeshift
    val show = d.show?.title?.takeIf { it.isNotBlank() }
    val title = show ?: d.title
    val subtitle = if (show != null) listOfNotNull(episodeCode(d), d.title.takeIf { it.isNotBlank() && it != show }).joinToString(" · ").ifBlank { null }
    else d.subtitle?.takeIf { it.isNotBlank() }
    val duration = s.titleDurationMs(ps.durationMs).takeIf { it > 0 } ?: d.durationMs?.takeIf { it > 0 }
    val position = if (live) 0L else (s.scrubTargetMs ?: s.titlePositionMs(ps.positionMs)).coerceIn(0, duration ?: Long.MAX_VALUE)
    val playing = s.room?.clock?.playing ?: (ps.isPlaying || (wantsToPlay && ps.buffering))
    return MiniPlayerInfo(
        id = d.id,
        title = title,
        subtitle = subtitle,
        image = d.poster,
        positionMs = position / 1000 * 1000,
        durationMs = if (live) null else duration,
        playing = playing,
        hasNext = s.upNext.next != null,
        hasPrevious = s.upNext.previous != null,
        live = live,
    )
}
