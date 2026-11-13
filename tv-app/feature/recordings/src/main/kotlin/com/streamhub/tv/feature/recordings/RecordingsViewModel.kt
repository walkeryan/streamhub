package com.streamhub.tv.feature.recordings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.Recording
import com.streamhub.tv.core.data.model.RecordingRule
import com.streamhub.tv.core.data.model.RecordingStates
import com.streamhub.tv.core.data.repo.RecordingsRepository
import com.streamhub.tv.core.data.repo.isUpcoming
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/** What the detail dialog is open on. */
sealed interface RecordingsSelection {
    data class Item(val recording: Recording) : RecordingsSelection
    data class Rule(val rule: RecordingRule) : RecordingsSelection
}

data class RecordingsState(
    val loading: Boolean = true,
    /** The lists could not be read; Retry is offered. */
    val error: UserError? = null,
    /** false until the server has somewhere to keep recordings. */
    val enabled: Boolean = true,
    /** This user may cancel, delete and change rules. */
    val canSchedule: Boolean = false,
    /** Scheduled or running, earliest first. */
    val upcoming: List<Recording> = emptyList(),
    /** Done, failed or missed, newest first. */
    val recorded: List<Recording> = emptyList(),
    val rules: List<RecordingRule> = emptyList(),
    val selected: RecordingsSelection? = null,
    /** An action on [selected] is running. */
    val busy: Boolean = false,
    /** The action on [selected] failed; shown in its dialog. */
    val actionError: UserError? = null,
    /** Bumped whenever a card may have gone, so the screen can put focus somewhere that still exists. */
    val changes: Int = 0,
) {
    val isEmpty: Boolean get() = upcoming.isEmpty() && recorded.isEmpty() && rules.isEmpty()
}

/** Upcoming (scheduled, recording) earliest first; everything else that is listed, newest first. */
internal fun splitRecordings(all: List<Recording>): Pair<List<Recording>, List<Recording>> {
    val listed = all.filter { it.state != RecordingStates.CANCELLED }
    val (upcoming, recorded) = listed.partition { it.isUpcoming() }
    return upcoming.sortedBy { it.startsAt } to recorded.sortedByDescending { it.startsAt }
}

@HiltViewModel
class RecordingsViewModel @Inject constructor(private val repo: RecordingsRepository) : ViewModel() {
    private val _state = MutableStateFlow(RecordingsState())
    val state: StateFlow<RecordingsState> = _state.asStateFlow()
    private var loadJob: Job? = null

    init { load() }

    fun load() {
        loadJob?.cancel()
        _state.update { it.copy(loading = it.isEmpty, error = null) }
        loadJob = viewModelScope.launch { reload() }
    }

    private suspend fun reload() {
        try {
            val overview = repo.recordings()
            // Rules are a second call; without them the recordings are still worth showing.
            val rules = if (overview.availability.enabled) {
                try { repo.rules() } catch (e: CancellationException) { throw e } catch (_: Exception) { _state.value.rules }
            } else emptyList()
            val (upcoming, recorded) = splitRecordings(overview.recordings)
            _state.update {
                it.copy(loading = false, error = null, enabled = overview.availability.enabled, canSchedule = overview.availability.canSchedule,
                    upcoming = upcoming, recorded = recorded, rules = rules)
            }
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { _state.update { it.copy(loading = false, error = ApiErrors.describe(e)) } }
    }

    fun open(recording: Recording) = _state.update { it.copy(selected = RecordingsSelection.Item(recording), actionError = null) }
    fun open(rule: RecordingRule) = _state.update { it.copy(selected = RecordingsSelection.Rule(rule), actionError = null) }
    fun dismiss() { if (!_state.value.busy) _state.update { it.copy(selected = null, actionError = null) } }

    /** Cancels an upcoming or running recording; deletes a finished one and its file. */
    fun removeSelected() = act { selection ->
        val item = selection as? RecordingsSelection.Item ?: return@act
        repo.remove(item.recording.id)
    }

    /** Disabling a rule drops the upcoming recordings it scheduled; enabling it schedules them again. */
    fun toggleSelectedRule() = act { selection ->
        val rule = (selection as? RecordingsSelection.Rule)?.rule ?: return@act
        repo.setRuleEnabled(rule.id, !rule.enabled)
    }

    /** Removes the rule and the upcoming recordings it scheduled; finished ones stay. */
    fun deleteSelectedRule() = act { selection ->
        val rule = (selection as? RecordingsSelection.Rule)?.rule ?: return@act
        repo.deleteRule(rule.id)
    }

    private fun act(action: suspend (RecordingsSelection) -> Unit) {
        val s = _state.value
        val selection = s.selected ?: return
        if (s.busy || !s.canSchedule) return
        _state.update { it.copy(busy = true, actionError = null) }
        viewModelScope.launch {
            try {
                action(selection)
                reload()
                _state.update { it.copy(busy = false, selected = null, changes = it.changes + 1) }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(busy = false, actionError = ApiErrors.describe(e)) } }
        }
    }
}
