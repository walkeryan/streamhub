package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.RewindCapability
import java.time.Instant
import kotlinx.coroutines.ensureActive

/** Advertised provider metadata, not proof that media exists or a local DVR buffer. */
fun archiveChoices(capability: RewindCapability): List<Instant> {
    if (capability.mode != "provider_archive" || capability.granularitySeconds != 60) return emptyList()
    val start = runCatching { Instant.parse(capability.advertisedStart) }.getOrNull() ?: return emptyList()
    val end = runCatching { Instant.parse(capability.advertisedEnd) }.getOrNull() ?: return emptyList()
    if (end <= start) return emptyList()
    return (1L..60L).map { end.minusSeconds(it * 60) }.filter { it >= start }
}

fun archiveSelectionValid(capability: RewindCapability, at: Instant): Boolean = archiveChoices(capability).contains(at)

class ExpiredArchiveSelection : IllegalArgumentException("Archive selection expired")

/** No descriptor from a canceled request or different profile/server may reach the player. */
suspend fun resolveArchiveSelection(
    channelId: String,
    at: Instant?,
    expectedScope: String,
    currentScope: suspend () -> String,
    capability: suspend (String) -> RewindCapability,
    resolve: suspend (com.streamhub.tv.core.data.model.PlaybackRequest) -> com.streamhub.tv.core.data.model.PlaybackDescriptor,
): com.streamhub.tv.core.data.model.PlaybackDescriptor {
    suspend fun checkScope() {
        kotlinx.coroutines.currentCoroutineContext().ensureActive()
        if (currentScope() != expectedScope) throw kotlinx.coroutines.CancellationException("Playback scope changed")
    }
    require(channelId.startsWith("ch:"))
    checkScope()
    if (at != null) {
        val fresh = capability(channelId)
        checkScope()
        if (!archiveSelectionValid(fresh, at)) throw ExpiredArchiveSelection()
    }
    val descriptor = resolve(com.streamhub.tv.core.data.model.PlaybackRequest(channelId, rewindAt=at?.toString()))
    checkScope()
    require(descriptor.id == channelId)
    if(at != null) require(!descriptor.live && descriptor.archiveStart != null && descriptor.archiveEnd != null)
    return descriptor
}
