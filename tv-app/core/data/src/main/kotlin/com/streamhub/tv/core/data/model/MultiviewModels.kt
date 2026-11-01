package com.streamhub.tv.core.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable data class PlaybackGeneration(val generation:String)
@Serializable data class MultiviewRequest(val panes:List<PlaybackRequest>,@SerialName("recovery_generation") val recoveryGeneration:String)
@Serializable data class MultiviewDescriptor(
    @SerialName("lease_id") val leaseId:String,
    @SerialName("expires_in_seconds") val expiresInSeconds:Int=60,
    val panes:List<PlaybackDescriptor>,
) {override fun toString():String="MultiviewDescriptor(leaseId=[REDACTED], panes=${panes.size})"}
@Serializable data class MultiviewRelease(@SerialName("lease_id") val leaseId:String) {
    override fun toString():String="MultiviewRelease(leaseId=[REDACTED])"
}
