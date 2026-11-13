package com.streamhub.tv.feature.requests

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.MediaRequest
import com.streamhub.tv.core.data.model.RequestStates
import com.streamhub.tv.core.data.repo.RequestsRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class RequestsTab { Mine, All }

data class RequestsState(
    val loading: Boolean = true,
    val error: UserError? = null,
    /** This user may approve and decline, and sees everyone's requests under "All requests". */
    val canApprove: Boolean = false,
    val tab: RequestsTab = RequestsTab.Mine,
    val mine: List<MediaRequest> = emptyList(),
    val all: List<MediaRequest> = emptyList(),
    /** The request whose dialog is open. */
    val selected: MediaRequest? = null,
    val busy: Boolean = false,
    /** Approve or Decline failed; shown in the dialog. */
    val actionError: UserError? = null,
) {
    val shown: List<MediaRequest> get() = if (tab == RequestsTab.All) all else mine
    /** Requests waiting for a decision, for the "All requests" chip. */
    val waiting: Int get() = all.count { it.state == RequestStates.PENDING_APPROVAL }
}

/** Only a request still waiting for a decision can be approved or declined. */
internal fun canDecide(state: RequestsState, request: MediaRequest): Boolean =
    state.canApprove && request.state == RequestStates.PENDING_APPROVAL

@HiltViewModel
class RequestsViewModel @Inject constructor(private val repo: RequestsRepository) : ViewModel() {
    private val _state = MutableStateFlow(RequestsState())
    val state: StateFlow<RequestsState> = _state.asStateFlow()
    private var loadJob: Job? = null

    init { load() }

    fun load() {
        loadJob?.cancel()
        _state.update { it.copy(loading = it.mine.isEmpty() && it.all.isEmpty(), error = null) }
        loadJob = viewModelScope.launch {
            try {
                val mine = repo.mine()
                // Everyone's list is a second call; if it is refused after all, the user is not an approver.
                val all = if (mine.canApprove) {
                    try { repo.all() } catch (e: CancellationException) { throw e } catch (_: Exception) { null }
                } else emptyList()
                _state.update {
                    val approver = mine.canApprove && all != null
                    it.copy(loading = false, error = null, canApprove = approver, mine = mine.requests, all = all.orEmpty(),
                        tab = if (approver) it.tab else RequestsTab.Mine)
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(loading = false, error = ApiErrors.describe(e)) } }
        }
    }

    fun show(tab: RequestsTab) = _state.update { if (tab == RequestsTab.All && !it.canApprove) it else it.copy(tab = tab) }
    fun open(request: MediaRequest) = _state.update { it.copy(selected = request, actionError = null) }
    fun dismiss() { if (!_state.value.busy) _state.update { it.copy(selected = null, actionError = null) } }

    fun approveSelected() = decide { repo.approve(it.id) }
    fun declineSelected() = decide { repo.decline(it.id) }

    private fun decide(action: suspend (MediaRequest) -> MediaRequest) {
        val s = _state.value
        val request = s.selected ?: return
        if (s.busy || !canDecide(s, request)) return
        _state.update { it.copy(busy = true, actionError = null) }
        viewModelScope.launch {
            try {
                val decided = action(request)
                fun List<MediaRequest>.updated() = map { if (it.id == decided.id) decided else it }
                _state.update { it.copy(busy = false, selected = null, mine = it.mine.updated(), all = it.all.updated()) }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(busy = false, actionError = ApiErrors.describe(e)) } }
        }
    }
}
