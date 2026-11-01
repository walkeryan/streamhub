package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.net.URI
import java.time.Instant

interface BufferLeaseApi {
    suspend fun acquire(token: String): BufferLease
    suspend fun heartbeat(action: BufferLeaseAction): BufferWindow
    suspend fun seek(action: BufferLeaseAction): BufferSeekResult
    suspend fun release(action: BufferLeaseAction)
}

/** Only descriptor-granted HLS tokens are accepted. Never replace this grant with a seek URL token. */
fun descriptorBufferToken(descriptor: PlaybackDescriptor): String? {
    if(!descriptor.bufferAvailable || !descriptor.live || descriptor.mode != PlaybackMode.HLS) return null
    val uri=runCatching { URI(descriptor.url ?: return null) }.getOrNull() ?: return null
    if(uri.scheme !in listOf("https","http")) return null
    return Regex("/stream/([^/]+)/index\\.m3u8$").find(uri.path ?: "")?.groupValues?.get(1)?.takeIf { it.isNotBlank() }
}

fun retainedBufferChoices(window: BufferWindow): List<Instant> {
    val start=runCatching { Instant.parse(window.start) }.getOrNull() ?: return emptyList()
    val end=runCatching { Instant.parse(window.end) }.getOrNull() ?: return emptyList()
    if(end <= start) return emptyList()
    return ((1L..60L).map { end.minusSeconds(it*60) }.filter { it >= start } + start).distinct()
}

class BufferPositionExpired : IllegalArgumentException("Position is outside the retained buffer")

/** Serialized lease operations; canceled acquisition releases any known lease before returning. */
class BufferLeaseSession(private val api: BufferLeaseApi) {
    private val mutex=Mutex()
    private var active: Pair<String,BufferLease>?=null
    val leaseId: String? get()=active?.second?.leaseId
    val window: BufferWindow? get()=active?.second?.window

    suspend fun open(descriptor: PlaybackDescriptor): BufferLease = mutex.withLock {
        check(active == null) { "Release previous lease first" }
        val token=descriptorBufferToken(descriptor) ?: throw IllegalArgumentException("No eligible descriptor grant")
        var obtained: BufferLease?=null
        try {
            val lease=api.acquire(token)
            obtained=lease
            currentCoroutineContext().ensureActive()
            active=token to lease
            lease
        } catch(e: Throwable) {
            obtained?.let { lease -> withContext(NonCancellable) { withTimeoutOrNull(5000) { runCatching { api.release(BufferLeaseAction(token,lease.leaseId)) } } } }
            throw e
        }
    }

    suspend fun heartbeat(paused: Boolean): BufferWindow = mutex.withLock {
        val (token,lease)=active ?: error("No active buffer lease")
        val updated=api.heartbeat(BufferLeaseAction(token,lease.leaseId,paused=paused))
        currentCoroutineContext().ensureActive()
        active=token to lease.copy(window=updated)
        updated
    }

    suspend fun seek(at: Instant?): BufferSeekResult = mutex.withLock {
        val (token,lease)=active ?: error("No active buffer lease")
        if(at != null) {
            val start=runCatching { Instant.parse(lease.window.start) }.getOrNull()
            val end=runCatching { Instant.parse(lease.window.end) }.getOrNull()
            if(start==null || end==null || at<start || at>=end) throw BufferPositionExpired()
        }
        val result=api.seek(BufferLeaseAction(token,lease.leaseId,at=at?.toString()))
        currentCoroutineContext().ensureActive()
        active=token to lease.copy(window=result.window)
        result
    }

    /** Release is best effort and bounded; server lease expiry is the fallback after network/auth loss. */
    suspend fun close(expectedLeaseId: String? = null): Boolean = withContext(NonCancellable) {
        withTimeoutOrNull(5000) {
            mutex.withLock {
                val (token,lease)=active ?: return@withLock true
                if(expectedLeaseId != null && lease.leaseId != expectedLeaseId) return@withLock true
                active=null
                runCatching { api.release(BufferLeaseAction(token,lease.leaseId)) }.isSuccess
            }
        } ?: false
    }
}
