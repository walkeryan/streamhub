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
 * Nothing comes back as a [AudioPlaybackAttempt.Choice] any more (the owner's decisions: "if I open a
 * channel, don't prompt me at all"; "it should never ask me if there is only one stream"; "if one
 * Spanish one sneaks through, my ears aren't going to bleed"). Provider audio is almost never verified,
 * so the chooser stood in front of nearly every film and every zap. The server's automatic variant (a
 * source with preferred audio first) plays, else the first source; pressing Play is the consent, sent
 * explicitly so a server that still checks audio plays it too, and the player still picks a tagged
 * track in the profile's languages when a file has several. The type stays for callers that match on it.
 */
suspend fun audioPlaybackAttempt(
    request: PlaybackRequest,
    preflight: Boolean,
    options: suspend (String, String?) -> PlaybackOptions,
    resolve: suspend (PlaybackRequest) -> PlaybackDescriptor,
    beforeResolve: suspend () -> Unit = {},
): AudioPlaybackAttempt {
    val channel = channelPlayback(request.id)
    // A source the viewer (or the web app) already named is played as named.
    var selected = if (request.variantId != null) request.copy(allowUnverifiedAudio = true) else request
    if (preflight && (request.id.startsWith("tmdb:") || request.id.startsWith("lib:") || channel)) {
        val choices = options(request.id, request.sourceId)
        currentCoroutineContext().ensureActive()
        if (choices.options.isNotEmpty()) {
            val best = choices.options.firstOrNull { it.variantId == choices.automaticVariantId }
                ?: choices.options.firstOrNull { it.preferredAudioAvailable }
                ?: choices.options.first()
            selected = request.copy(variantId = best.variantId, allowUnverifiedAudio = true)
        }
    }
    currentCoroutineContext().ensureActive()
    beforeResolve()
    currentCoroutineContext().ensureActive()
    val descriptor = resolve(selected)
    currentCoroutineContext().ensureActive()
    return AudioPlaybackAttempt.Ready(descriptor, selected)
}
