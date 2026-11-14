package com.streamhub.tv.shell

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.repo.UpdateException
import com.streamhub.tv.core.data.repo.UpdateInfo
import com.streamhub.tv.core.data.repo.UpdateInstaller
import com.streamhub.tv.core.data.repo.UpdateRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class TvSettingsState(
    val serverUrl: String? = null,
    val checking: Boolean = false,
    /** A newer TV build the server offers; null when there is none or no update service. */
    val update: UpdateInfo? = null,
    val updateBusy: Boolean = false,
    val updateStatus: String? = null,
)

/** The native "TV settings": what belongs to this TV rather than to a person (everything else is in the web app). */
@HiltViewModel
class TvSettingsViewModel @Inject constructor(
    sessions: SessionStore,
    private val updates: UpdateRepository,
    private val installer: UpdateInstaller,
) : ViewModel() {
    private val _state = MutableStateFlow(TvSettingsState())
    val state: StateFlow<TvSettingsState> = _state.asStateFlow()
    private var check: Job? = null

    init {
        viewModelScope.launch { sessions.session.collect { s -> _state.update { it.copy(serverUrl = s.serverUrl) } } }
        checkForUpdate()
    }

    fun checkForUpdate() {
        if (check?.isActive == true || _state.value.updateBusy) return
        _state.update { it.copy(checking = true, updateStatus = null) }
        check = viewModelScope.launch {
            try {
                val info = updates.check()
                _state.update { it.copy(checking = false, update = info, updateStatus = if (info == null) "This is the newest version your server offers." else null) }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(checking = false, updateStatus = ApiErrors.describe(e).body) } }
        }
    }

    /** Download, verify against the published SHA-256, then hand over to the system installer. */
    fun installUpdate() {
        val info = _state.value.update ?: return
        if (_state.value.updateBusy) return
        _state.update { it.copy(updateBusy = true, updateStatus = "Downloading…") }
        viewModelScope.launch {
            try {
                var shown = -1
                val apk = installer.download(info) { fraction ->
                    val percent = fraction?.let { (it * 100).toInt() }
                    if (percent != null && percent != shown) { shown = percent; _state.update { it.copy(updateStatus = "Downloading… $percent%") } }
                }
                installer.launchInstaller(apk)
                _state.update { it.copy(updateBusy = false, updateStatus = "Download verified. Confirm in the installer to finish. If no installer opened, allow StreamHub to install apps in this TV's settings, then choose Install again.") }
            } catch (e: CancellationException) { throw e }
            catch (e: UpdateException) { _state.update { it.copy(updateBusy = false, updateStatus = e.message) } }
            catch (_: android.content.ActivityNotFoundException) { _state.update { it.copy(updateBusy = false, updateStatus = "This TV has no installer that can open the update.") } }
            catch (e: Exception) { _state.update { it.copy(updateBusy = false, updateStatus = ApiErrors.describe(e).body) } }
        }
    }
}
