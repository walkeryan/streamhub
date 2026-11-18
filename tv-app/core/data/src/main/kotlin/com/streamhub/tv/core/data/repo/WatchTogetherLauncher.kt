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
 * episode), a live channel, and the player's own menu for whatever is playing. Each view model owns
 * one, on its own scope. [open] creates the room, [start] joins it, [cancel] closes a room nobody
 * was let into.
 *
 * By default Start takes a seat and opens the player on it. A player that is already open passes
 * [join] instead: it moves its own playback into the room, and a failure it throws is shown in the
 * dialog like a refused join, with the room and its code still on screen.
 */
class WatchTogetherLauncher(
    private val rooms: RoomDirectory,
    private val scope: CoroutineScope,
    private val join: (suspend (room: Room, allowUnverifiedAudio: Boolean) -> Unit)? = null,
) {
    private val _state = MutableStateFlow<WatchTogether?>(null)
    val state: StateFlow<WatchTogether?> = _state.asStateFlow()
    /** What the room is created on, kept for "Try again". */
    private var target: Target? = null
    private data class Target(val titleId: String, val variantId: String?, val audioTrack: Int?)

    /** [variantId] and [audioTrack]: the source and audio track being played, when the room should open on them. */
    fun open(titleId: String, title: String, subject: RoomSubject, variantId: String? = null, audioTrack: Int? = null) {
        val phase = _state.value?.phase
        if (phase is WatchTogether.Phase.Creating || phase is WatchTogether.Phase.Ready) return
        val wanted = Target(titleId, variantId, audioTrack)
        target = wanted
        val opening = WatchTogether(title, subject, WatchTogether.Phase.Creating)
        _state.value = opening
        scope.launch {
            val next = try {
                val room = rooms.create(wanted.titleId, wanted.variantId, wanted.audioTrack)
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
        val wanted = target ?: return
        _state.value = null
        open(wanted.titleId, failed.title, failed.subject, wanted.variantId, wanted.audioTrack)
    }

    fun start() {
        val current = _state.value ?: return
        val ready = current.phase as? WatchTogether.Phase.Ready ?: return
        if (ready.starting) return
        _state.value = current.copy(phase = ready.copy(starting = true, error = null))
        scope.launch {
            try {
                val custom = join
                if (custom != null) {
                    custom(ready.room, ready.needsAudioConsent)
                    _state.value = null
                } else {
                    val key = rooms.join(ready.room.code, allowUnverifiedAudio = ready.needsAudioConsent)
                    _state.value = null
                    rooms.openPlayer(key)
                }
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
