package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

@OptIn(ExperimentalCoroutinesApi::class)
class ArchiveIsolationTest {
    private val cap=RewindCapability("provider_archive","2026-11-01T18:00:00Z","2026-11-01T19:00:00Z")
    private val at=Instant.parse("2026-11-01T18:30:00Z")
    @Test fun cancelBackPreventsLateArchiveStart()=runTest {
        var resolves=0
        val job=launch {
            resolveArchiveSelection("ch:s:7",at,"server/profile",{"server/profile"},{withContext(NonCancellable){delay(100)};cap},{resolves++;error("unexpected")})
        }
        runCurrent();job.cancel();advanceUntilIdle();assertEquals(0,resolves)
    }
    @Test fun changedServerOrProfileRejectsPendingSelection()=runTest {
        var scope="one/profile";var resolves=0
        try {
            resolveArchiveSelection("ch:s:7",at,scope,{scope},{scope="two/profile";cap},{resolves++;error("unexpected")})
            fail("Expected cancellation")
        } catch (_:CancellationException) { }
        assertEquals(0,resolves)
    }
    @Test fun mismatchedChannelAndLiveArchiveDescriptorAreRejected()=runTest {
        for (descriptor in listOf(PlaybackDescriptor("ch:other:1",PlaybackMode.PROXY),PlaybackDescriptor("ch:s:7",PlaybackMode.PROXY,live=true))) {
            try { resolveArchiveSelection("ch:s:7",at,"p",{"p"},{cap},{descriptor});fail("Expected rejection") }
            catch (_:IllegalArgumentException) { }
        }
    }
    @Test fun goLiveHasNoArchiveAndDoesNotRequestCapability()=runTest {
        var sent:PlaybackRequest?=null
        val result=resolveArchiveSelection("ch:s:7",null,"p",{"p"},{error("unnecessarymetadata")},{sent=it;PlaybackDescriptor(it.id,PlaybackMode.PROXY,live=true)})
        assertTrue(result.live);assertNull(sent!!.rewindAt)
    }
    @Test fun archiveConsentSurvivesFreshWindowCheckAndStopsOnlyBeforeResolve()=runTest {
        val order=mutableListOf<String>()
        val accepted=PlaybackRequest("ch:s:7",variantId="ch:s:7",allowUnverifiedAudio=true,rewindAt=at.toString())
        resolveArchiveSelection("ch:s:7",at,"p",{"p"},{order+="capability";cap},{assertEquals(accepted,it);order+="resolve";PlaybackDescriptor(it.id,PlaybackMode.PROXY,archiveStart=cap.advertisedStart,archiveEnd=cap.advertisedEnd)},request=accepted,beforeResolve={order+="stop-old"})
        assertEquals(listOf("capability","stop-old","resolve"),order)
    }
    @Test fun expiredSelectionDoesNotStopCurrentStream()=runTest {
        var stopped=false
        try {
            resolveArchiveSelection("ch:s:7",Instant.parse("2026-11-01T17:00:00Z"),"p",{"p"},{cap},{error("mustnotresolve")},beforeResolve={stopped=true})
            fail("Expected expired selection")
        } catch (_:ExpiredArchiveSelection) { }
        assertFalse(stopped)
    }

}
