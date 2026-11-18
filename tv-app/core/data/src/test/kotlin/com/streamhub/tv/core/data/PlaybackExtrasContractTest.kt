package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMarker
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The player revamp's optional descriptor fields: read when they are there, harmless when they are not or are wrong. */
class PlaybackExtrasContractTest {
    private fun decode(extra: String) = StreamHubJson.decodeFromString(
        PlaybackDescriptor.serializer(),
        """{"id":"lib:plex-1:ep:201","mode":"direct","url":"http://hub.invalid/s/1","title":"Episode 1"$extra}""",
    )

    @Test fun anEpisodeCarriesItsMarkersShowNeighboursAndThumbnails() {
        val d = decode(
            ""","markers":[{"type":"intro","start_ms":12000,"end_ms":71000},{"type":"credits","start_ms":2860000,"end_ms":3000000}],
               "show":{"id":"lib:plex-1:show:7","title":"Garden Stories"},"season_number":2,"episode_number":1,
               "next":{"id":"lib:plex-1:ep:202","title":"Episode 2","subtitle":"S2 · E2","image":"http://hub.invalid/i/2","duration_ms":3000000},
               "previous":{"id":"lib:plex-1:ep:112","title":"Finale","subtitle":"S1 · E12","image":null,"duration_ms":null},
               "thumbnails":{"url":"http://hub.invalid/api/v1/images/abc","interval_ms":10000}""",
        )
        assertEquals(listOf(PlaybackMarker("intro", 12_000, 71_000), PlaybackMarker("credits", 2_860_000, 3_000_000)), d.markers)
        assertEquals("Garden Stories", d.show?.title)
        assertEquals(2, d.seasonNumber)
        assertEquals(1, d.episodeNumber)
        assertEquals("lib:plex-1:ep:202", d.next?.id)
        assertEquals("S2 · E2", d.next?.subtitle)
        assertEquals(3_000_000L, d.next?.durationMs)
        assertNull(d.previous?.image)
        assertEquals(10_000L, d.thumbnails?.intervalMs)
    }

    @Test fun anOlderServerSendsNoneOfThemAndPlaysAsBefore() {
        val d = decode("")
        assertTrue(d.markers.isEmpty())
        assertNull(d.show); assertNull(d.next); assertNull(d.previous); assertNull(d.thumbnails)
        assertNull(d.seasonNumber); assertNull(d.episodeNumber)
    }

    @Test fun explicitNullsAreTheSameAsAbsent() {
        val d = decode(""","markers":null,"show":null,"next":null,"previous":null,"thumbnails":null,"season_number":null""")
        assertTrue(d.markers.isEmpty())
        assertNull(d.next)
        assertNull(d.thumbnails)
    }

    @Test fun aMalformedExtraIsLeftOutInsteadOfFailingPlayback() {
        val d = decode(
            ""","markers":[{"type":"intro","start_ms":"soon"},{"type":"commercial","start_ms":1,"end_ms":2},
               {"type":"credits","start_ms":500,"end_ms":100},{"type":"credits","start_ms":100,"end_ms":500}],
               "show":"Garden Stories","next":[1,2],"previous":{"id":5},"thumbnails":{"url":{"x":1},"interval_ms":10000}""",
        )
        assertEquals("only the one well-formed credits marker survives", listOf(PlaybackMarker("credits", 100, 500)), d.markers)
        assertNull(d.show)
        assertNull(d.next)
        assertNull(d.thumbnails)
        assertEquals("Episode 1", d.title)
    }

    @Test fun markersThatAreNotAListAreNone() {
        assertTrue(decode(""","markers":{"type":"intro"}""").markers.isEmpty())
    }
}
