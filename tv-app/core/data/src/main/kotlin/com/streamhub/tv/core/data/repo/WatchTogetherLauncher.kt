package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.Room
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** What a room is opened on, for the words of its dialog. */
enum class RoomSubject {
    /** A film or one episode: everyone plays the same title from the room's position. */
    Title,
    /** A series: the room opens on the next episode the server resolves when the first person joins. */
    Series,
    /** A live channel: there is no position, only play and pause for everyone. */
    Channel,
}

/** The "Watch together" dialog: a room is created first, shown with its code, then joined with Start. */
data class WatchTogether(
    /** What the room is for, as its dialog names it. */
    val title: String,
    val subject: RoomSubject,
    val phase: Phase,
) {
    sealed interface Phase {
        data object Creating : Phase
        data class Failed(val error: UserError) : Phase
        data class Ready(
            val room: Room,
            /** `<server>/room/<code>` for the QR code. */
            val shareUrl: String?,
            val starting: Boolean = false,
            val error: UserError? = null,
            /** The room's source has no confirmed audio in this profile's languages; Start asks to go ahead with it. */
            val needsAudioConsent: Boolean = false,
        ) : Phase
    }
}

/**
 * "Watch together" wherever it is offered: a film's or an episode's page, a series (the next
 * episode) and a live channel. Each view model owns one, on its own scope. [open] creates the
 * room, [start] joins it and opens the player, [cancel] closes a room nobody was let into.
 */
class WatchTogetherLauncher(private val rooms: RoomDirectory, private val scope: CoroutineScope) {
    private val _state = MutableStateFlow<WatchTogether?>(null)
    val state: StateFlow<WatchTogether?> = _state.asStateFlow()
    private var titleId: String? = null

    fun open(titleId: String, title: String, subject: RoomSubject) {
        val phase = _state.value?.phase
        if (phase is WatchTogether.Phase.Creating || phase is WatchTogether.Phase.Ready) return
        this.titleId = titleId
        val opening = WatchTogether(title, subject, WatchTogether.Phase.Creating)
        _state.value = opening
        scope.launch {
            val next = try {
                val room = rooms.create(titleId)
                WatchTogether.Phase.Ready(room, rooms.shareUrl(room))
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { WatchTogether.Phase.Failed(ApiErrors.describe(e)) }
            // Cancelled while the room was being made: nobody will come for it.
            if (_state.value !== opening) { if (next is WatchTogether.Phase.Ready) closeQuietly(next.room.code); return@launch }
            _state.value = opening.copy(phase = next)
        }
    }

    /** "Try again" after the room could not be created. */
    fun retry() {
        val failed = _state.value?.takeIf { it.phase is WatchTogether.Phase.Failed } ?: return
        val id = titleId ?: return
        _state.value = null
        open(id, failed.title, failed.subject)
    }

    fun start() {
        val current = _state.value ?: return
        val ready = current.phase as? WatchTogether.Phase.Ready ?: return
        if (ready.starting) return
        _state.value = current.copy(phase = ready.copy(starting = true, error = null))
        scope.launch {
            try {
                val key = rooms.join(ready.room.code, allowUnverifiedAudio = ready.needsAudioConsent)
                _state.value = null
                rooms.openPlayer(key)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                val consent = (e as? ApiException)?.error?.code == "audio_selection_required" && !ready.needsAudioConsent
                _state.update {
                    it?.copy(phase = ready.copy(starting = false, needsAudioConsent = consent || ready.needsAudioConsent, error = if (consent) null else ApiErrors.describe(e)))
                }
            }
        }
    }

    /** Closes the dialog; a room nobody was let into is closed again rather than left to expire. */
    fun cancel() {
        val phase = _state.value?.phase
        if (phase is WatchTogether.Phase.Ready && phase.starting) return
        _state.value = null
        if (phase is WatchTogether.Phase.Ready) scope.launch { closeQuietly(phase.room.code) }
    }

    private suspend fun closeQuietly(code: String) {
        try { rooms.close(code) } catch (e: CancellationException) { throw e } catch (_: Exception) { }
    }
}
