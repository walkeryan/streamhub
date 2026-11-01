package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.*
import java.net.URI

fun validMultiviewPair(owner:Session,request:MultiviewRequest,result:MultiviewDescriptor):Boolean {
    if(request.panes.size!=2 || result.panes.size!=2 || request.panes.map{it.id}.distinct().size!=2 || result.leaseId.isBlank()) return false
    val origin=runCatching{URI(owner.apiBase ?: return false)}.getOrNull() ?: return false
    if(origin.userInfo!=null || origin.host==null || origin.scheme !in listOf("https","http"))return false
    fun port(uri:URI)=if(uri.port>=0)uri.port else if(uri.scheme.equals("https",true))443 else 80
    return result.panes.zip(request.panes).all {(descriptor,wanted)->
        val uri=runCatching{URI(descriptor.url ?: return false)}.getOrNull() ?: return false
        descriptor.id==wanted.id && wanted.id.startsWith("ch:") && descriptor.mode==PlaybackMode.HLS && descriptor.live &&
            !descriptor.bufferAvailable && descriptor.archiveStart==null && descriptor.archiveEnd==null &&
            uri.scheme.equals(origin.scheme,true) && uri.host.equals(origin.host,true) && port(uri)==port(origin) && uri.userInfo==null
    }
}

/** Atomic admission only: a canceled/late pair is released before either media player can attach. */
class AtomicMultiviewLease(
    private val reserve:suspend(MultiviewRequest,Session)->MultiviewDescriptor,
    private val release:suspend(String,Session)->Unit,
) {
    private var owned:Pair<Session,MultiviewDescriptor>?=null
    val descriptor:MultiviewDescriptor? get()=owned?.second
    suspend fun open(owner:Session,request:MultiviewRequest,stillCurrent:suspend()->Boolean):MultiviewDescriptor {
        check(owned==null)
        var acquired:MultiviewDescriptor?=null
        try {
            currentCoroutineContext().ensureActive()
            if(!stillCurrent())throw CancellationException("Multiview intent changed")
            withContext(NonCancellable) {withTimeout(20_000) {acquired=reserve(request,owner)}}
            currentCoroutineContext().ensureActive()
            if(!stillCurrent())throw CancellationException("Multiview intent changed")
            val result=requireNotNull(acquired)
            require(validMultiviewPair(owner,request,result)) {"Unsupported multiview response"}
            owned=owner to result;acquired=null
            return result
        } finally {
            acquired?.let {late->withContext(NonCancellable){withTimeoutOrNull(5000){runCatching{release(late.leaseId,owner)}}}}
        }
    }
    suspend fun close():Boolean {
        val current=owned ?: return true
        owned=null
        return withContext(NonCancellable){withTimeoutOrNull(5000){runCatching{release(current.second.leaseId,current.first)}.isSuccess} ?: false}
    }
}
