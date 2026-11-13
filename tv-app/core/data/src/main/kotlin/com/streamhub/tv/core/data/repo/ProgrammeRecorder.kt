package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.Programme
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * The Record actions of a programme dialog, shared by the guide and Live TV (each view model owns
 * one, on its own scope). One action at a time; [State.outcome] is what the dialog then says.
 */
class ProgrammeRecorder(
    private val recordings: RecordingsRepository,
    private val scope: CoroutineScope,
    /** A single programme was scheduled: its new recording state, for the badge where it is listed. */
    private val onRecorded: (Programme, String?) -> Unit = { _, _ -> },
) {
    data class State(val busy: Boolean = false, val outcome: RecordOutcome? = null)

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    /** Whether recording exists on this server and this user may schedule. */
    val availability: StateFlow<RecordingAvailability> get() = recordings.availability

    fun recordOnce(p: Programme) = run { recordings.recordOnce(p).also { if (it is RecordOutcome.Done) onRecorded(p, it.state) } }
    fun recordSeries(p: Programme) = run { recordings.recordSeries(p) }
    fun recordTeam(team: String) = run { recordings.recordTeam(team) }

    /** The dialog was closed (or opened on another programme): forget the last answer. */
    fun reset() { if (!_state.value.busy) _state.value = State() }

    private fun run(action: suspend () -> RecordOutcome) {
        if (_state.value.busy) return
        _state.value = State(busy = true)
        scope.launch {
            val outcome = try { action() } finally { _state.update { it.copy(busy = false) } }
            _state.value = State(outcome = outcome)
        }
    }
}
