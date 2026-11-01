package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

@OptIn(ExperimentalCoroutinesApi::class)
class BufferLeaseSessionTest {
    private val descriptor=PlaybackDescriptor("ch:s:7",PlaybackMode.HLS,url="https://fixture.invalid/api/v1/stream/original-grant/index.m3u8",live=true,bufferAvailable=true)
    private val window=BufferWindow(start="2026-11-01T18:00:00Z",end="2026-11-01T18:00:05Z",bufferedSeconds=5.0,recordingEnabled=true)
    private inner class FakeApi:BufferLeaseApi {
        val calls=mutableListOf<String>()
        val actions=mutableListOf<BufferLeaseAction>()
        var failAcquire=false
        var delayedAcquire=false
        override suspend fun acquire(token:String):BufferLease {
            calls+="lease:$token"
            if(delayedAcquire) withContext(NonCancellable){delay(100)}
            if(failAcquire) error("fixtureleasefailure")
            return BufferLease("lease-one","session-one",window)
        }
        override suspend fun heartbeat(action:BufferLeaseAction):BufferWindow {calls+="heartbeat";actions+=action;return window}
        override suspend fun seek(action:BufferLeaseAction):BufferSeekResult {calls+="seek";actions+=action;return BufferSeekResult("https://fixture.invalid/api/v1/stream/seek-token/index.m3u8",window)}
        override suspend fun release(action:BufferLeaseAction) {calls+="release";actions+=action}
    }
    @Test fun descriptorFlagAndExactHlsGrantRequired() {
        assertEquals("original-grant",descriptorBufferToken(descriptor))
        assertNull(descriptorBufferToken(descriptor.copy(bufferAvailable=false)))
        assertNull(descriptorBufferToken(descriptor.copy(live=false)))
        assertNull(descriptorBufferToken(descriptor.copy(url="https://fixture.invalid/other.m3u8")))
        assertNull(descriptorBufferToken(descriptor.copy(mode=PlaybackMode.PROXY)))
    }
    @Test fun leaseBeforeAttachAndNoAttachOnFailure()=runTest {
        val api=FakeApi();val session=BufferLeaseSession(api)
        session.open(descriptor);api.calls+="attach"
        assertEquals(listOf("lease:original-grant","attach"),api.calls)
        assertEquals(5.0,session.window!!.bufferedSeconds,0.0)
        session.close()
        api.failAcquire=true
        try {session.open(descriptor);fail("shouldnotattach")}catch(_:IllegalStateException){}
        assertNull(session.leaseId)
    }
    @Test fun pausedHeartbeatAndSeekRetainOriginalGrantAndReleaseOnce()=runTest {
        val api=FakeApi();val session=BufferLeaseSession(api)
        session.open(descriptor)
        session.heartbeat(paused=true)
        assertTrue(api.actions.last().paused)
        session.seek(Instant.parse(window.start))
        session.seek(null)
        session.close();session.close()
        assertEquals(1,api.calls.count {it=="release"})
        assertTrue(api.actions.all {it.streamToken=="original-grant" && it.leaseId=="lease-one"})
        assertNull(api.actions[2].at)
    }
    @Test fun canceledAcquisitionReleasesKnownLeaseAndNeverAttaches()=runTest {
        val api=FakeApi().apply{delayedAcquire=true};val session=BufferLeaseSession(api)
        var attached=false
        val job=launch {session.open(descriptor);attached=true}
        runCurrent();job.cancel();advanceUntilIdle()
        assertFalse(attached);assertNull(session.leaseId)
        assertEquals(1,api.calls.count{it=="release"})
    }
    @Test fun sourceReplacementReleasesBeforeNewLease()=runTest {
        val api=FakeApi();val session=BufferLeaseSession(api)
        session.open(descriptor);session.close()
        session.open(descriptor.copy(url="https://fixture.invalid/api/v1/stream/new-profile-grant/index.m3u8"))
        assertEquals(listOf("lease:original-grant","release","lease:new-profile-grant"),api.calls)
    }
    @Test fun onlyRetainedWindowSeekableNotTargetHour()=runTest {
        assertEquals(listOf(Instant.parse(window.start)),retainedBufferChoices(window))
        assertTrue(retainedBufferChoices(window.copy(start=null,end=null)).isEmpty())
        val api=FakeApi();val session=BufferLeaseSession(api);session.open(descriptor)
        for(at in listOf(Instant.parse(window.start).minusSeconds(1),Instant.parse(window.end))) {
            try{session.seek(at);fail("expiredposition")}catch(_:BufferPositionExpired){}
        }
        assertFalse(api.calls.contains("seek"))
    }
    @Test fun staleOwnerCannotReleaseCurrentLease()=runTest {
        val api=FakeApi();val session=BufferLeaseSession(api);session.open(descriptor)
        assertTrue(session.close("different-old-lease"))
        assertEquals("lease-one",session.leaseId)
        assertFalse(api.calls.contains("release"))
        session.close("lease-one")
        assertNull(session.leaseId)
    }

}
