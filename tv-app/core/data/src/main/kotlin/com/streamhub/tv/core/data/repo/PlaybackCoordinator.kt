package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.handoff.DeepLinkLauncher
import com.streamhub.tv.core.data.handoff.HandoffResult
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.model.PlaybackRequest
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
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val cache = ConcurrentHashMap<String, PlaybackDescriptor>()
    private var inFlight: Job? = null

    private val _events = MutableSharedFlow<PlayEvent>(extraBufferCapacity = 4, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val events: SharedFlow<PlayEvent> = _events

    fun play(id: String, sourceId: String? = null, catchupProgrammeId: String? = null) {
        inFlight?.cancel()
        inFlight = scope.launch {
            runCatching { api.resolve(PlaybackRequest(id = id, sourceId = sourceId, catchupProgrammeId = catchupProgrammeId)) }
                .onSuccess { d -> dispatch(d) }
                .onFailure { _events.emit(PlayEvent.Error(it.message ?: "Playback failed")) }
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

    /** Used by the player for channel zapping / last-channel: resolves without emitting navigation. */
    suspend fun resolveDirect(id: String, sourceId: String? = null, catchupProgrammeId: String? = null): PlaybackDescriptor =
        api.resolve(PlaybackRequest(id = id, sourceId = sourceId, catchupProgrammeId = catchupProgrammeId))
}
