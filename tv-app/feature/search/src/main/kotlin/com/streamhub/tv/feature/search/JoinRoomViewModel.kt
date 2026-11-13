package com.streamhub.tv.feature.search

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.Room
import com.streamhub.tv.core.data.repo.RoomRepository
import com.streamhub.tv.core.data.room.RoomCode
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class JoinRoomState(
    /** The code typed so far (see [RoomCode]). */
    val entry: String = "",
    val looking: Boolean = false,
    /** The room the complete code names. */
    val room: Room? = null,
    val error: UserError? = null,
    val joining: Boolean = false,
    /** The room's source has no confirmed audio in this profile's languages; Join asks to go ahead with it. */
    val needsAudioConsent: Boolean = false,
)

/** "Join a room": six characters, then the room is looked up and shown before it is joined. */
@HiltViewModel
class JoinRoomViewModel @Inject constructor(private val rooms: RoomRepository) : ViewModel() {
    private val _state = MutableStateFlow(JoinRoomState())
    val state: StateFlow<JoinRoomState> = _state.asStateFlow()
    private var lookup: Job? = null

    fun type(c: Char) = edit(RoomCode.append(_state.value.entry, c))
    fun backspace() = edit(RoomCode.backspace(_state.value.entry))
    fun clear() = edit("")

    private fun edit(next: String) {
        val s = _state.value
        if (next == s.entry || s.joining) return
        lookup?.cancel()
        _state.value = JoinRoomState(entry = next)
        if (RoomCode.isComplete(next)) lookUp()
    }

    /** Also "Try again" after a lookup that failed for a reason other than the code. */
    fun lookUp() {
        val code = _state.value.entry
        if (!RoomCode.isComplete(code)) return
        lookup?.cancel()
        _state.update { it.copy(looking = true, error = null, room = null, needsAudioConsent = false) }
        lookup = viewModelScope.launch {
            try {
                val room = rooms.lookup(code)
                _state.update { if (it.entry == code) it.copy(looking = false, room = room) else it }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { if (it.entry == code) it.copy(looking = false, error = ApiErrors.describe(e)) else it } }
        }
    }

    fun join() {
        val s = _state.value
        val room = s.room ?: return
        if (s.joining) return
        _state.update { it.copy(joining = true, error = null) }
        viewModelScope.launch {
            try {
                val key = rooms.join(room.code, allowUnverifiedAudio = s.needsAudioConsent)
                _state.update { it.copy(joining = false) }
                rooms.openPlayer(key)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                val consent = (e as? ApiException)?.error?.code == "audio_selection_required" && !s.needsAudioConsent
                _state.update { it.copy(joining = false, needsAudioConsent = consent || s.needsAudioConsent, error = if (consent) null else ApiErrors.describe(e)) }
            }
        }
    }
}
