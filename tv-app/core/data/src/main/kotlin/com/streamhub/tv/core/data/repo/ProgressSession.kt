package com.streamhub.tv.core.data.repo

import io.ktor.client.plugins.auth.AuthCircuitBreaker
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.header
import io.ktor.client.request.setBody
import io.ktor.http.HttpHeaders
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.sameSwitchSession
import com.streamhub.tv.core.data.model.ProgressReport

/** Captured destination/auth cannot be replaced by a later profile/server while the request is sent. */
data class BoundProgressRequest(val url:String,val accessToken:String,val body:ProgressReport) {
    override fun toString():String="BoundProgressRequest(url=[BOUND SERVER], accessToken=[REDACTED], expectedProfileId=${body.expectedProfileId})"
}
fun boundProgressRequest(owner:Session,current:Session,report:ProgressReport):BoundProgressRequest? {
    if(!sameSwitchSession(owner,current)) return null
    val profile=owner.profile?.id ?: return null
    val base=owner.apiBase ?: return null
    if(runCatching{java.net.URI(base).userInfo!=null}.getOrDefault(true)) return null
    val access=current.accessToken?.takeIf{it.isNotBlank()} ?: return null
    return BoundProgressRequest("$base/playback/progress",access,report.copy(expectedProfileId=profile))
}

/** Never let the shared bearer plugin refresh or replace a captured owner's credential. */
fun HttpRequestBuilder.bindProgressSession(request:BoundProgressRequest) {
    attributes.put(AuthCircuitBreaker,Unit)
    header(HttpHeaders.Authorization,"Bearer ${request.accessToken}")
    setBody(request.body)
}
