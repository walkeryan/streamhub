package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.model.PlaybackShow
import com.streamhub.tv.core.data.model.PlaybackThumbnails
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlayerLabelsTest {
    private val ep = PlaybackDescriptor(
        id = "lib:p:ep:1", mode = PlaybackMode.DIRECT, title = "Episode 1",
        show = PlaybackShow("lib:p:show:1", "Garden Stories"), seasonNumber = 2, episodeNumber = 1,
    )

    @Test fun autoplayIsOnUnlessThePreferenceSaysFalse() {
        assertTrue(autoplayNextPref(null))
        assertTrue(autoplayNextPref(JsonNull))
        assertTrue(autoplayNextPref(JsonPrimitive(true)))
        assertFalse(autoplayNextPref(JsonPrimitive(false)))
        assertTrue("a string is not a stored false", autoplayNextPref(JsonPrimitive("false")))
    }

    @Test fun anEpisodeReadsShowSeasonEpisodeOverItsName() {
        assertEquals("Garden Stories · Season 2 · Episode 1" to "Episode 1", titleBlock(ep))
        assertEquals("S2 · E1", episodeCode(ep))
        assertEquals("Garden Stories · Season 2 · Episode 1" to "Garden Stories", titleBlock(ep.copy(title = "")))
    }

    @Test fun aFilmKeepsItsOwnTitleAndSubtitle() {
        val film = PlaybackDescriptor(id = "tmdb:movie:1", mode = PlaybackMode.DIRECT, title = "The Long Walk", subtitle = "2025")
        assertEquals("2025" to "The Long Walk", titleBlock(film))
        assertNull(titleBlock(film.copy(subtitle = " ")).first)
        assertNull(episodeCode(film))
    }

    @Test fun aThumbnailIsTheGrantUrlWithThePositionRoundedToTheInterval() {
        val t = PlaybackThumbnails("https://hub.invalid/api/v1/images/abc", 10_000)
        assertEquals("https://hub.invalid/api/v1/images/abc?ms=120000", thumbnailUrl(t, 125_400, null))
        assertEquals("https://hub.invalid/i?sig=x&ms=0", thumbnailUrl(PlaybackThumbnails("https://hub.invalid/i?sig=x", 10_000), -5, null))
        assertEquals("http://hub.invalid:8787/api/v1/images/abc?ms=10000", thumbnailUrl(PlaybackThumbnails("/api/v1/images/abc", 10_000), 19_999, "http://hub.invalid:8787/"))
    }

    @Test fun noUsableThumbnailsMeansNone() {
        assertNull(thumbnailUrl(null, 0, null))
        assertNull(thumbnailUrl(PlaybackThumbnails("https://hub.invalid/i", 0), 0, null))
        assertNull(thumbnailUrl(PlaybackThumbnails("", 10_000), 0, null))
        assertNull(thumbnailUrl(PlaybackThumbnails("/rel", 10_000), 0, null))
        assertNull(thumbnailUrl(PlaybackThumbnails("javascript:x", 10_000), 0, "http://hub.invalid"))
    }

    @Test fun trackLabelsAreShortenedForTheControlRow() {
        assertEquals("English 5.1", shortTrackLabel("English · 5.1 · EAC3"))
        assertEquals("Track 1", shortTrackLabel("Track 1"))
    }

    @Test fun clockReadsMinutesOrHours() {
        assertEquals("0:00", clock(-1))
        assertEquals("31:42", clock(1_902_000))
        assertEquals("1:02:03", clock(3_723_000))
    }
}
