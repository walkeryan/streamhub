package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackState
import com.streamhub.tv.core.data.model.ProgressReport
import com.streamhub.tv.core.data.model.UpNext
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import javax.inject.Inject
import javax.inject.Singleton

/** What a resolve said about a title, kept so a later progress report can be read with it. */
data class PlaybackFacts(
    val id: String,
    val title: String,
    val subtitle: String?,
    val poster: String?,
    val durationMs: Long?,
    val live: Boolean,
    /** Episodes only: the show's name, and where in it this episode is. */
    val showTitle: String? = null,
    val seasonNumber: Int? = null,
    val episodeNumber: Int? = null,
    /** Episodes only: the one after it (across into the next season). */
    val next: UpNext? = null,
)

/** A progress report the server took, for the person [profileId], with what the resolve said about the title. */
data class ProgressEvent(
    val id: String,
    val profileId: String?,
    val positionMs: Long,
    val durationMs: Long?,
    val state: PlaybackState,
    val facts: PlaybackFacts?,
)

/**
 * The player's progress, as seen by StreamHubApi: every resolve and every progress report the server
 * accepted. Whatever wants to follow playback (the TV home screen's "Continue watching") listens to
 * [events] here instead of hooking into the player, which stays unaware of it.
 *
 * In memory only, and small: the facts of the last [MAX_FACTS] titles resolved.
 */
@Singleton
class PlaybackReports @Inject constructor() {
    private val facts = object : LinkedHashMap<String, PlaybackFacts>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, PlaybackFacts>?) = size > MAX_FACTS
    }

    private val _events = MutableSharedFlow<ProgressEvent>(extraBufferCapacity = 32, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val events: SharedFlow<ProgressEvent> = _events.asSharedFlow()

    fun resolved(d: PlaybackDescriptor) {
        if (d.id.isBlank()) return
        val f = PlaybackFacts(
            id = d.id, title = d.title, subtitle = d.subtitle, poster = d.poster, durationMs = d.durationMs, live = d.live,
            showTitle = d.show?.title?.takeIf { it.isNotBlank() }, seasonNumber = d.seasonNumber, episodeNumber = d.episodeNumber,
            next = d.next?.takeIf { it.id.isNotBlank() },
        )
        synchronized(facts) { facts[d.id] = f }
    }

    fun reported(r: ProgressReport) {
        val f = synchronized(facts) { facts[r.id] }
        _events.tryEmit(ProgressEvent(r.id, r.expectedProfileId, r.positionMs, r.durationMs, r.state, f))
    }

    fun facts(id: String): PlaybackFacts? = synchronized(facts) { facts[id] }

    private companion object { const val MAX_FACTS = 32 }
}
