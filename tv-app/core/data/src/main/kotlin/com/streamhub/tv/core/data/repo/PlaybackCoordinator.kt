package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.StreamHubApi
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
    data class Error(val message: String) : PlayEvent
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
    private var inFlight: Job? = null
    private var choiceOwner: CatalogScope? = null
    private var activeOwner: String? = null
    private var stopActive: (() -> Unit)? = null
    fun registerActivePlayer(owner: String, stop: () -> Unit) { activeOwner = owner; stopActive = stop }
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

    private fun submit(request: PlaybackRequest, preflight: Boolean = true, expectedOwner: CatalogScope? = null) {
        inFlight?.cancel()
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
                    is AudioPlaybackAttempt.Ready -> { choiceOwner=null; dispatch(result.descriptor) }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.emit(PlayEvent.Error(e.message ?: "Playback failed"))
            }
        }
    }

    private suspend fun dispatch(d: PlaybackDescriptor) {
        if (d.mode == PlaybackMode.HANDOFF) {
            val h = d.handoff
            if (h == null) _events.emit(PlayEvent.Error("Hand-off target missing"))
            else _events.emit(PlayEvent.Handoff(launcher.launch(h)))
        } else {
            val key = put(d)
            _events.emit(PlayEvent.StartPlayer(key))
        }
    }

    fun put(d: PlaybackDescriptor): String {
        val key = "${d.id}#${System.nanoTime()}"
        cache[key] = d
        return key
    }

    fun take(key: String): PlaybackDescriptor? = cache[key]
    fun release(key: String) { cache.remove(key) }

    // Channel zapping uses the player's scoped audio preflight; no unguarded direct resolver.
}
