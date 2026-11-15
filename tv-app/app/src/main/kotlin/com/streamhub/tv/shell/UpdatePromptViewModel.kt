package com.streamhub.tv.shell

import android.os.SystemClock
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.repo.UpdateInfo
import com.streamhub.tv.core.data.repo.UpdateInstaller
import com.streamhub.tv.core.data.repo.UpdateRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class UpdatePromptState(
    /** The newer build being offered; null when the prompt is not up. */
    val offer: UpdateInfo? = null,
    val busy: Boolean = false,
    val status: String? = null,
)

/**
 * "A newer StreamHub is available": asked by the shell itself, so a TV is not left on an old build until
 * someone happens to open its settings (see [UpdatePromptRules]). A failed check says nothing: the TV
 * settings screen is where update problems are shown.
 */
@HiltViewModel
class UpdatePromptViewModel @Inject constructor(
    private val updates: UpdateRepository,
    private val installer: UpdateInstaller,
) : ViewModel() {
    private val _state = MutableStateFlow(UpdatePromptState())
    val state: StateFlow<UpdatePromptState> = _state.asStateFlow()
    private var lastCheckMs: Long? = null
    private var declined: Int? = null
    private var checking = false

    fun checkIfDue(onWebApp: Boolean, playerOpen: Boolean) {
        val now = SystemClock.elapsedRealtime()
        if (checking || _state.value.offer != null || !UpdatePromptRules.dueToCheck(onWebApp, playerOpen, lastCheckMs, now)) return
        checking = true
        lastCheckMs = now
        viewModelScope.launch {
            try {
                val offer = UpdatePromptRules.offer(updates.check(), declined)
                if (offer != null) _state.update { it.copy(offer = offer, status = null) }
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { /* quiet: TV settings shows why on request */ }
            finally { checking = false }
        }
    }

    fun later() {
        val offer = _state.value.offer ?: return
        if (_state.value.busy) return
        declined = offer.versionCode
        _state.value = UpdatePromptState()
    }

    fun install() {
        val offer = _state.value.offer ?: return
        if (_state.value.busy) return
        _state.update { it.copy(busy = true) }
        viewModelScope.launch {
            val last = downloadAndInstall(installer, offer) { s -> _state.update { it.copy(status = s) } }
            _state.update { it.copy(busy = false, status = last) }
        }
    }
}
