package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive

sealed interface AudioPlaybackAttempt {
    data class Choice(val request: PlaybackRequest, val options: PlaybackOptions) : AudioPlaybackAttempt
    /** [request] is what was actually resolved (automatic or chosen variant included), for a later re-resolve. */
    data class Ready(val descriptor: PlaybackDescriptor, val request: PlaybackRequest) : AudioPlaybackAttempt
}

/** Live TV, a guide catch-up of it, or its archive: the server resolves all three as the channel itself. */
fun channelPlayback(id: String): Boolean = id.startsWith("ch:") || id.startsWith("prog:")

/**
 * One request owns its consent. Cancellation wins even if an HTTP adapter returns late.
 *
 * A channel never comes back as a [AudioPlaybackAttempt.Choice] (the owner's decision: "if I open a
 * channel, don't prompt me at all"). IPTV carries a provider language hint, not audio tracks, so the
 * chooser stood in front of every zap; a channel has one source, so there was nothing to choose. Opening
 * it is the consent, sent explicitly so a server that still checks channel audio plays it too, and the
 * player still picks a tagged track in the profile's languages when a stream has several.
 */
suspend fun audioPlaybackAttempt(
    request: PlaybackRequest,
    preflight: Boolean,
    options: suspend (String, String?) -> PlaybackOptions,
    resolve: suspend (PlaybackRequest) -> PlaybackDescriptor,
    beforeResolve: suspend () -> Unit = {},
): AudioPlaybackAttempt {
    val channel = channelPlayback(request.id)
    var selected = if (channel && request.variantId != null) request.copy(allowUnverifiedAudio = true) else request
    if (preflight && (request.id.startsWith("tmdb:") || request.id.startsWith("lib:") || channel)) {
        val choices = options(request.id, request.sourceId)
        currentCoroutineContext().ensureActive()
        if (channel && choices.options.isNotEmpty()) {
            val own = choices.options.firstOrNull { it.variantId == choices.automaticVariantId } ?: choices.options.first()
            selected = request.copy(variantId = own.variantId, allowUnverifiedAudio = true)
        } else if (choices.options.isNotEmpty()) {
            val automatic = choices.options.firstOrNull { it.variantId == choices.automaticVariantId }
                ?.takeIf { it.preferredAudioAvailable || choices.preferredLanguages.isEmpty() }
            if (automatic == null) return AudioPlaybackAttempt.Choice(request, choices)
            selected = request.copy(variantId = automatic.variantId, allowUnverifiedAudio = false)
        }
    }
    currentCoroutineContext().ensureActive()
    beforeResolve()
    currentCoroutineContext().ensureActive()
    val descriptor = resolve(selected)
    currentCoroutineContext().ensureActive()
    return AudioPlaybackAttempt.Ready(descriptor, selected)
}
