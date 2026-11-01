package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.*
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import kotlinx.serialization.encodeToString
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AtomicMultiviewTest {
    private val owner=Session("https://fixture.invalid","access","refresh",Profile("one","One"),"generation")
    private val request=MultiviewRequest(listOf(PlaybackRequest("ch:s:one",variantId="ch:s:one",allowUnverifiedAudio=true),PlaybackRequest("ch:s:two",variantId="ch:s:two")),"epoch")
    private fun pair()=MultiviewDescriptor("fixture-lease",60,request.panes.map{PlaybackDescriptor(it.id,PlaybackMode.HLS,"https://fixture.invalid/api/v1/stream/fixture/index.m3u8",live=true)})
    @Test fun serializationKeepsAtomicEpochExactVariantsAndPerPaneConsent() {
        val json=StreamHubJson.encodeToString(request)
        assertTrue(json.contains("recovery_generation"));assertTrue(json.contains("allow_unverified_audio"))
        assertEquals(request,StreamHubJson.decodeFromString<MultiviewRequest>(json))
        assertFalse(pair().toString().contains("fixture-lease"))
    }
    @Test fun rejectsUnsupportedOrMismatchedMediaBeforeAttach() {
        assertTrue(validMultiviewPair(owner,request,pair()))
        val first=pair().panes.first()
        listOf(first.copy(mode=PlaybackMode.DIRECT),first.copy(live=false),first.copy(bufferAvailable=true),first.copy(archiveStart="2026-01-01T00:00:00Z"),first.copy(id="ch:s:other"),first.copy(url="https://other.invalid/media"),first.copy(url="https://secret@fixture.invalid/media")).forEach {bad->
            assertFalse(validMultiviewPair(owner,request,pair().copy(panes=listOf(bad,pair().panes[1]))))
        }
        assertFalse(validMultiviewPair(owner,request.copy(panes=listOf(request.panes[0],request.panes[0])),pair()))
        assertTrue(validMultiviewPair(owner,request,pair().copy(panes=pair().panes.map{it.copy(url="https://fixture.invalid:443/media")})))
    }
    @Test fun admissionUsesOneAtomicRequestAndReleaseIsIdempotent()=runTest {
        var reserves=0;var releases=0
        val lease=AtomicMultiviewLease({received,session->assertEquals(request,received);assertEquals(owner,session);reserves++;pair()},{id,session->assertEquals("fixture-lease",id);assertEquals(owner,session);releases++})
        assertEquals(pair(),lease.open(owner,request){true});assertEquals(1,reserves)
        assertTrue(lease.close());assertTrue(lease.close());assertEquals(1,releases)
    }
    @Test fun canceledAdmissionReleasesLateGrantWithoutReturningMedia()=runTest {
        val response=CompletableDeferred<MultiviewDescriptor>();var released=0;var attached=false
        val lease=AtomicMultiviewLease({_,_->response.await()},{_,_->released++})
        val job=launch{lease.open(owner,request){true};attached=true}
        runCurrent();job.cancel();response.complete(pair());advanceUntilIdle()
        assertFalse(attached);assertEquals(1,released);assertNull(lease.descriptor)
    }
    @Test fun profileOrIntentChangeWhileAdmissionPendingReleasesBoth()=runTest {
        var current=true;var released=0
        val lease=AtomicMultiviewLease({_,_->current=false;pair()},{_,_->released++})
        try {lease.open(owner,request){current};fail("Changed intent must not attach")}catch(_:CancellationException){}
        assertEquals(1,released);assertNull(lease.descriptor)
    }
    @Test fun capacityFailureNeverCreatesPartialPlayback()=runTest {
        var released=0;var returned=false
        val lease=AtomicMultiviewLease({_,_->error("source capacity one")},{_,_->released++})
        try{lease.open(owner,request){true};returned=true}catch(_:IllegalStateException){}
        assertFalse(returned);assertEquals(0,released);assertNull(lease.descriptor)
    }
    @Test fun cleanupFailureIsReportedWithoutRepeatedImplicitRelease()=runTest {
        var attempts=0
        val lease=AtomicMultiviewLease({_,_->pair()},{_,_->attempts++;error("offline")})
        lease.open(owner,request){true};assertFalse(lease.close());assertTrue(lease.close());assertEquals(1,attempts)
    }
    @Test fun invalidGrantedPairIsReleasedAndAdmissionTimesOutBoundedly()=runTest {
        var released=0
        val invalid=AtomicMultiviewLease({_,_->pair().copy(panes=listOf(pair().panes[0]))},{_,_->released++})
        try{invalid.open(owner,request){true};fail("Invalid response")}catch(_:IllegalArgumentException){}
        assertEquals(1,released)
        val timeout=AtomicMultiviewLease({_,_->delay(25_000);pair()},{_,_->released++})
        try{timeout.open(owner,request){true};fail("Timeout")}catch(_:TimeoutCancellationException){}
        assertNull(timeout.descriptor)
    }
}
