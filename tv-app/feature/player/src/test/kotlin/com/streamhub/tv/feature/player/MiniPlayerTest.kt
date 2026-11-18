package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.model.PlaybackShow
import com.streamhub.tv.core.data.model.UpNext
import com.streamhub.tv.core.player.PlayerUiState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** What the web app's mini player bar is told while the native player is in picture-in-picture. */
class MiniPlayerTest {
    private val episode = PlaybackDescriptor(
        id = "lib:p:ep:3", mode = PlaybackMode.DIRECT, title = "Episode 3", poster = "https://hub.invalid/i/3", durationMs = 3_070_000,
        show = PlaybackShow("lib:p:show:1", "Garden Stories"), seasonNumber = 2, episodeNumber = 3,
        next = UpNext("lib:p:ep:4", "Episode 4"), previous = UpNext("lib:p:ep:2", "Episode 2"),
    )

    private fun state(d: PlaybackDescriptor?) = PlayerScreenState(descriptor = d, live = d?.live == true, upNext = UpNextFlow.plan(d, null, false))

    @Test fun nothingLoadedIsNoBar() = assertNull(miniPlayerInfo(PlayerScreenState(), PlayerUiState(), wantsToPlay = true))

    @Test fun anEpisodeReadsAsItsShowWithTheEpisodeUnderneath() {
        val info = miniPlayerInfo(state(episode), PlayerUiState(isPlaying = true, positionMs = 662_499, durationMs = 3_070_000), wantsToPlay = true)!!
        assertEquals("lib:p:ep:3", info.id)
        assertEquals("Garden Stories", info.title)
        assertEquals("S2 · E3 · Episode 3", info.subtitle)
        assertEquals("https://hub.invalid/i/3", info.image)
        assertEquals("whole seconds", 662_000L, info.positionMs)
        assertEquals(3_070_000L, info.durationMs)
        assertTrue(info.playing)
        assertTrue(info.hasNext)
        assertTrue(info.hasPrevious)
        assertFalse(info.live)
    }

    @Test fun aSeekUnderWayShowsWhereItIsGoing() {
        val info = miniPlayerInfo(state(episode).copy(scrubTargetMs = 700_400), PlayerUiState(positionMs = 600_000, durationMs = 3_070_000), true)!!
        assertEquals(700_000L, info.positionMs)
    }

    @Test fun bufferingWhileMeantToPlayStillReadsAsPlaying() {
        assertTrue(miniPlayerInfo(state(episode), PlayerUiState(isPlaying = false, buffering = true), wantsToPlay = true)!!.playing)
        assertFalse(miniPlayerInfo(state(episode), PlayerUiState(isPlaying = false, buffering = true), wantsToPlay = false)!!.playing)
    }

    @Test fun aFilmHasNoNeighboursAndUsesTheDescriptorLengthBeforeThePlayerKnowsIt() {
        val film = PlaybackDescriptor(id = "tmdb:movie:1", mode = PlaybackMode.DIRECT, title = "The Long Walk", subtitle = "2025", durationMs = 6_000_000)
        val info = miniPlayerInfo(state(film), PlayerUiState(), true)!!
        assertEquals("The Long Walk", info.title)
        assertEquals("2025", info.subtitle)
        assertEquals(6_000_000L, info.durationMs)
        assertFalse(info.hasNext)
        assertFalse(info.hasPrevious)
    }

    @Test fun liveHasNoPositionOrLength() {
        val ch = PlaybackDescriptor(id = "ch:7", mode = PlaybackMode.PROXY, title = "News 24", live = true, subtitle = "Evening bulletin")
        val info = miniPlayerInfo(state(ch), PlayerUiState(isPlaying = true, positionMs = 99_000), true)!!
        assertTrue(info.live)
        assertEquals(0L, info.positionMs)
        assertNull(info.durationMs)
        assertFalse(info.hasNext)
    }

    @Test fun aPositionIsNeverPastTheEnd() {
        val info = miniPlayerInfo(state(episode), PlayerUiState(positionMs = 9_999_999, durationMs = 3_070_000), true)!!
        assertEquals(3_070_000L, info.positionMs)
    }
}
