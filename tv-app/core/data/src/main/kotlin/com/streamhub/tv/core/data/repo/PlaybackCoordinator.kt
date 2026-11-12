package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.auth.SessionStore
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import com.streamhub.tv.core.data.handoff.DeepLinkLauncher
import com.streamhub.tv.core.data.handoff.HandoffResult
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.model.PlaybackRequest
import com.streamhub.tv.core.data.model.PlaybackOptions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

sealed interface PlayEvent {
    /** Navigate straight to the full-screen player; descriptor is cached under [key]. */
    data class StartPlayer(val key: String) : PlayEvent
    data class Handoff(val result: HandoffResult) : PlayEvent
    data class ChooseAudio(val request: PlaybackRequest, val options: PlaybackOptions) : PlayEvent
    /** Shown as a dialog; "Try again" calls [PlaybackCoordinator.retryLast] when [UserError.canRetry]. */
    data class Error(val error: UserError) : PlayEvent
}

/**
 * The "press Play" path (ARCHITECTURE.md): POST /playback/resolve, then either hand the
 * descriptor to the player screen with no intermediate screen, or launch the hand-off app.
 * The resolved descriptor is cached so the player does not resolve twice.
 */
@Singleton
class PlaybackCoordinator @Inject constructor(
    private val api: StreamHubApi,
    private val launcher: DeepLinkLauncher,
    private val sessions: SessionStore,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val cache = ConcurrentHashMap<String, PlaybackDescriptor>()
    private val requests = ConcurrentHashMap<String, PlaybackRequest>()
    private var inFlight: Job? = null
    /** The attempt behind the last [PlayEvent.Error], kept whole so a retry passes the same checks. */
    private var lastFailed: Attempt? = null
    private data class Attempt(val request: PlaybackRequest, val preflight: Boolean, val expectedOwner: CatalogScope?)
    private var choiceOwner: CatalogScope? = null
    private var activeOwner: String? = null
    private var stopActive: (suspend () -> Unit)? = null
    fun registerActivePlayer(owner: String, stop: suspend () -> Unit) { activeOwner = owner; stopActive = stop }
    fun unregisterActivePlayer(owner: String) { if(activeOwner == owner) { activeOwner=null; stopActive=null } }


    private val _events = MutableSharedFlow<PlayEvent>(extraBufferCapacity = 4, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val events: SharedFlow<PlayEvent> = _events

    fun play(id: String, sourceId: String? = null, catchupProgrammeId: String? = null) {
        submit(PlaybackRequest(id = id, sourceId = sourceId, catchupProgrammeId = catchupProgrammeId))
    }

    fun chooseAudio(request: PlaybackRequest, variantId: String, confirmed: Boolean) {
        submit(request.copy(variantId = variantId, allowUnverifiedAudio = confirmed), preflight = false, expectedOwner=choiceOwner ?: return)
    }

    fun cancelPending() { inFlight?.cancel(); choiceOwner=null }

    /** "Try again" on the error dialog. A no-op once the failure has been dismissed or superseded. */
    fun retryLast() { val attempt = lastFailed ?: return; submit(attempt.request, attempt.preflight, attempt.expectedOwner) }
    fun dismissError() { lastFailed = null }

    /**
     * Resolves [request] again without navigating. The player's "Try again" uses this instead of
     * replaying its descriptor: the stream URL in an old descriptor carries a token that may have expired.
     */
    suspend fun resolveAgain(request: PlaybackRequest): PlaybackDescriptor = api.resolve(request)

    /** Called before committing a profile switch, while the old profile can still release its leases. */
    suspend fun clearForProfileSwitch() {
        cancelPending()
        val owner=activeOwner
        val stop=stopActive
        stop?.invoke()
        cache.clear(); requests.clear(); lastFailed = null
        if(activeOwner==owner) { activeOwner=null; stopActive=null }
    }

    private fun submit(request: PlaybackRequest, preflight: Boolean = true, expectedOwner: CatalogScope? = null) {
        inFlight?.cancel()
        lastFailed = null
        inFlight = scope.launch {
            try {
                val owner=sessions.current().catalogScope()
                if(expectedOwner != null && expectedOwner != owner) return@launch
                val result=audioPlaybackAttempt(request, preflight, api::playbackOptions, api::resolve, {
                    currentCoroutineContext().ensureActive()
                    if(sessions.current().catalogScope()!=owner) throw CancellationException("Profile changed")
                    stopActive?.invoke()
                })
                currentCoroutineContext().ensureActive()
                if(sessions.current().catalogScope()!=owner) return@launch
                when (result) {
                    is AudioPlaybackAttempt.Choice -> { choiceOwner=owner; _events.emit(PlayEvent.ChooseAudio(result.request, result.options)) }
                    is AudioPlaybackAttempt.Ready -> { choiceOwner=null; dispatch(result.descriptor, result.request) }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                lastFailed = Attempt(request, preflight, expectedOwner)
                _events.emit(PlayEvent.Error(ApiErrors.describe(e)))
            }
        }
    }

    private suspend fun dispatch(d: PlaybackDescriptor, request: PlaybackRequest) {
        if (d.mode == PlaybackMode.HANDOFF) {
            val h = d.handoff
            if (h == null) _events.emit(PlayEvent.Error(UserError("Can't open this title", "The server didn't say which app plays it. Try another source from the title's page.", canRetry = false)))
            else _events.emit(PlayEvent.Handoff(launcher.launch(h)))
        } else {
            val key = put(d, request)
            _events.emit(PlayEvent.StartPlayer(key))
        }
    }

    fun put(d: PlaybackDescriptor, request: PlaybackRequest? = null): String {
        val key = "${d.id}#${System.nanoTime()}"
        cache[key] = d
        if (request != null) requests[key] = request
        return key
    }

    fun take(key: String): PlaybackDescriptor? = cache[key]
    /** The accepted request (source and audio choice included) that produced [take]'s descriptor. */
    fun requestFor(key: String): PlaybackRequest? = requests[key]
    fun release(key: String) { cache.remove(key); requests.remove(key) }

    // Channel zapping uses the player's scoped audio preflight; no unguarded direct resolver.
}
