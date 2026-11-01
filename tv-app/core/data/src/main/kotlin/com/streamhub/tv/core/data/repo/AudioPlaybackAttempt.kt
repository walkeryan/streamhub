package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive

sealed interface AudioPlaybackAttempt {
    data class Choice(val request: PlaybackRequest, val options: PlaybackOptions) : AudioPlaybackAttempt
    data class Ready(val descriptor: PlaybackDescriptor) : AudioPlaybackAttempt
}

/** One request owns its consent. Cancellation wins even if an HTTP adapter returns late. */
suspend fun audioPlaybackAttempt(
    request: PlaybackRequest,
    preflight: Boolean,
    options: suspend (String, String?) -> PlaybackOptions,
    resolve: suspend (PlaybackRequest) -> PlaybackDescriptor,
    beforeResolve: suspend () -> Unit = {},
): AudioPlaybackAttempt {
    var selected = request
    if (preflight && (request.id.startsWith("tmdb:") || request.id.startsWith("lib:") || request.id.startsWith("ch:") || request.id.startsWith("prog:"))) {
        val choices = options(request.id, request.sourceId)
        currentCoroutineContext().ensureActive()
        if (choices.options.isNotEmpty()) {
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
    return AudioPlaybackAttempt.Ready(descriptor)
}
