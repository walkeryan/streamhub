package com.streamhub.tv.feature.home

import com.streamhub.tv.core.data.model.*
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

class SportsRecordingsTest {
    private val recording=SportsRecording("lib:plex-source:12","Actual library title","plex-source","Library","Soccer")
    @Test fun exactLibrarySourceOnlyAndNoImplicitConsent() {
        assertEquals("lib:plex-source:12" to "plex-source",sportsRecordingPlayback(recording))
        assertNull(sportsRecordingPlayback(recording.copy(sourceId="another")))
        assertNull(sportsRecordingPlayback(recording.copy(titleId="ch:plex-source:12")))
        assertNull(sportsRecordingPlayback(recording.copy(live=true)))
        assertNull(sportsRecordingPlayback(recording.copy(kind="event")))
        val (id,source)=sportsRecordingPlayback(recording)!!
        val request=PlaybackRequest(id,sourceId=source)
        assertFalse(request.allowUnverifiedAudio);assertNull(request.variantId)
    }
    @Test fun AudioHintDoesNotBecomeVerifiedAndReleaseIsNotEventDate() {
        assertEquals("Audio unverified · Provider hint: eng",sportsRecordingAudio(recording.copy(languageHint="eng")))
        assertEquals("Audio unverified",sportsRecordingAudio(recording.copy(audioVerified=true)))
        assertEquals("Verified audio: fra",sportsRecordingAudio(recording.copy(audioVerified=true,audioLanguages=listOf("fra"))))
        assertEquals("Original release date unavailable",sportsRecordingDate(recording))
        assertEquals("Original release date unavailable",sportsRecordingDate(recording.copy(originallyAvailableAt="invalid")))
        assertEquals("Original release: 2024-02-20",sportsRecordingDate(recording.copy(originallyAvailableAt="2024-02-20")))
    }
    @Test fun sharedFiltersExcludeGuideDateStatusAndBoundPages() {
        val params=sportsRecordingParameters(NativeSportsFilters(language="eng",sport="Soccer",date="2026-11-01",status="current",query="  actual  "),40)
        assertEquals(mapOf("language" to "eng","sport" to "Soccer","q" to "actual","offset" to "40","limit" to "40"),params)
        assertEquals("100",sportsRecordingParameters(NativeSportsFilters(),-5,500)["limit"])
        assertEquals("0",sportsRecordingParameters(NativeSportsFilters(),-5)["offset"])
        assertEquals(40,sportsRecordingsNext(SportsRecordingsPage(total=81)))
        assertNull(sportsRecordingsNext(SportsRecordingsPage(total=81,offset=80)))
        assertNull(sportsRecordingsNext(SportsRecordingsPage(total=50,limit=0)))
    }
    @Test fun contractPreservesExactIdentityUnknownAudioAndTruncation() {
        val page=Json.decodeFromString<SportsRecordingsPage>("""{"items":[{"title_id":"lib:plex:8","title":"Library replay","source_id":"plex","source_name":"Plex","sport":"Hockey","audio_languages":[],"audio_verified":false,"originally_available_at":null}],"total":45,"limit":40,"offset":0,"truncated":true}""")
        assertTrue(page.truncated);assertFalse(page.items.single().live)
        assertEquals("lib:plex:8" to "plex",sportsRecordingPlayback(page.items.single()))
        assertEquals("Audio unverified",sportsRecordingAudio(page.items.single()))
    }
}
