package com.streamhub.tv.feature.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.ChannelGroup
import com.streamhub.tv.core.data.model.ChannelGroupPatch
import com.streamhub.tv.core.data.model.HomeLayout
import com.streamhub.tv.core.data.model.HomeLayoutRow
import com.streamhub.tv.core.data.model.LanguageCount
import com.streamhub.tv.core.data.model.LoginRequest
import com.streamhub.tv.core.data.model.Profile
import com.streamhub.tv.core.data.model.Row
import com.streamhub.tv.core.data.model.Settings
import com.streamhub.tv.core.data.repo.LocalPrefs
import com.streamhub.tv.core.ui.theme.ThemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class SettingsSection(val label: String) {
    Profiles("Profiles"), Theme("Theme"), Languages("Languages"), Rows("Home rows"),
    Parental("Parental controls"), Playback("Playback"), About("About"),
}

data class SettingsState(
    val section: SettingsSection = SettingsSection.Profiles,
    val profiles: List<Profile> = emptyList(),
    val current: Profile? = null,
    val languages: List<LanguageCount> = emptyList(),
    val groups: List<ChannelGroup> = emptyList(),
    val rows: List<Row> = emptyList(),
    val rowOrder: List<String> = emptyList(),
    val hiddenRows: Set<String> = emptySet(),
    val serverSettings: Settings? = null,
    val serverUrl: String? = null,
    val message: String? = null,
    val pinPromptFor: Profile? = null,
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val session: SessionStore,
    private val prefs: LocalPrefs,
    private val themes: ThemeRepository,
) : ViewModel() {
    private val _state = MutableStateFlow(SettingsState())
    val state: StateFlow<SettingsState> = _state.asStateFlow()

    val selectedTheme = themes.selectedThemeId.stateIn(viewModelScope, SharingStarted.Eagerly, ThemeRepository.DEFAULT_THEME)
    val reducedMotion = prefs.reducedMotion.stateIn(viewModelScope, SharingStarted.Eagerly, false)

    init { load() }

    private fun pid() = _state.value.current?.id ?: "default"

    fun load() = viewModelScope.launch {
        val s = session.current()
        _state.update { it.copy(current = s.profile, serverUrl = s.serverUrl) }
        launch { runCatching { api.profiles() }.onSuccess { p -> _state.update { it.copy(profiles = p) } } }
        launch { runCatching { api.channelLanguages() }.onSuccess { l -> _state.update { it.copy(languages = l) } } }
        launch { runCatching { api.channelGroups() }.onSuccess { g -> _state.update { it.copy(groups = g) } } }
        launch { runCatching { api.settings() }.onSuccess { st -> _state.update { it.copy(serverSettings = st) } } }
        launch {
            runCatching { api.home() }.onSuccess { h ->
                val server = s.profile?.id?.let { p -> runCatching { api.homeLayout(p) }.getOrNull() }
                val order = server?.rows?.takeIf { it.isNotEmpty() }?.sortedBy { it.sort ?: Int.MAX_VALUE }?.map { it.id } ?: prefs.rowOrder(pid()).first()
                val hidden = server?.rows?.takeIf { it.isNotEmpty() }?.filter { it.hidden }?.map { it.id }?.toSet() ?: prefs.hiddenRows(pid()).first()
                val rows = h.rows.sortedBy { r -> order.indexOf(r.id).let { i -> if (i < 0) Int.MAX_VALUE else i } }
                _state.update { it.copy(rows = rows, rowOrder = rows.map { r -> r.id }, hiddenRows = hidden) }
            }
        }
    }

    fun select(section: SettingsSection) = _state.update { it.copy(section = section) }

    // ---- Theme: focus previews live, OK commits ------------------------------------------------
    fun previewTheme(id: String?) = themes.preview(id)
    fun selectTheme(id: String) = viewModelScope.launch {
        themes.select(id)
        _state.value.current?.let { p ->
            val updated = p.copy(themeId = id)
            runCatching { api.updateProfile(updated) }
            session.updateProfile(updated)
            _state.update { it.copy(current = updated) }
        }
    }
    fun setReducedMotion(on: Boolean) = viewModelScope.launch { prefs.setReducedMotion(on) }

    // ---- Languages (profile.languages, ISO 639-3) ---------------------------------------------
    fun toggleLanguage(code: String) = viewModelScope.launch {
        val p = _state.value.current ?: return@launch
        val langs = if (code in p.languages) p.languages - code else p.languages + code
        val updated = p.copy(languages = langs)
        runCatching { api.updateProfile(updated) }.onSuccess {
            session.updateProfile(updated)
            _state.update { it.copy(current = updated) }
        }
    }

    // ---- Home rows ----------------------------------------------------------------------------
    fun moveRow(id: String, delta: Int) = viewModelScope.launch {
        val order = _state.value.rowOrder.toMutableList()
        val i = order.indexOf(id); val j = i + delta
        if (i < 0 || j !in order.indices) return@launch
        order.removeAt(i); order.add(j, id)
        prefs.setRowOrder(pid(), order)
        _state.update { s -> s.copy(rowOrder = order, rows = order.mapNotNull { rid -> s.rows.firstOrNull { it.id == rid } }) }
        pushLayout()
    }

    private suspend fun pushLayout() {
        val p = _state.value.current ?: return
        val s = _state.value
        val layout = HomeLayout(s.rowOrder.mapIndexed { i, id -> HomeLayoutRow(id = id, hidden = id in s.hiddenRows, sort = i) })
        runCatching { api.putHomeLayout(p.id, layout) }
    }

    fun toggleRowHidden(id: String) = viewModelScope.launch {
        val h = _state.value.hiddenRows.let { if (id in it) it - id else it + id }
        prefs.setHiddenRows(pid(), h)
        _state.update { it.copy(hiddenRows = h) }
        pushLayout()
    }

    // ---- Parental / groups --------------------------------------------------------------------
    fun toggleGroupHidden(g: ChannelGroup) = viewModelScope.launch {
        runCatching { api.patchChannelGroup(g.id, ChannelGroupPatch(hidden = !g.hidden)) }
            .onSuccess { _state.update { s -> s.copy(groups = s.groups.map { if (it.id == g.id) it.copy(hidden = !g.hidden) else it }) } }
    }

    fun setPin(pin: String?) = viewModelScope.launch {
        val p = _state.value.current ?: return@launch
        runCatching { api.setPin(p.id, pin?.takeIf { it.isNotBlank() }) }
            .onSuccess {
                val updated = p.copy(hasPin = !pin.isNullOrBlank())
                session.updateProfile(updated)
                _state.update { it.copy(current = updated, message = if (pin.isNullOrBlank()) "PIN removed" else "PIN set") }
            }
            .onFailure { e -> _state.update { it.copy(message = e.message) } }
    }

    // ---- Profiles -----------------------------------------------------------------------------
    fun switchProfile(p: Profile, pin: String? = null) {
        if (p.hasPin && pin == null) { _state.update { it.copy(pinPromptFor = p) }; return }
        viewModelScope.launch {
            runCatching { api.login(LoginRequest(profileId = p.id, pin = pin)) }
                .onSuccess { tokens ->
                    session.saveTokens(tokens)
                    themes.select(tokens.profile.themeId)
                    _state.update { it.copy(current = tokens.profile, pinPromptFor = null, message = "Switched to ${tokens.profile.name}") }
                }
                .onFailure { e -> _state.update { it.copy(message = "Couldn't switch: ${e.message}", pinPromptFor = null) } }
        }
    }

    fun cancelPin() = _state.update { it.copy(pinPromptFor = null) }

    fun patchServer(s: Settings) = viewModelScope.launch {
        runCatching { api.patchSettings(s) }.onSuccess { _state.update { it.copy(serverSettings = (it.serverSettings ?: Settings()).merge(s)) } }
    }

    fun unpair() = viewModelScope.launch { session.clearTokens() }

    private fun Settings.merge(o: Settings) = Settings(
        tmdbApiKeySet = o.tmdbApiKeySet ?: tmdbApiKeySet, region = o.region ?: region,
        sleepTimerMin = o.sleepTimerMin ?: sleepTimerMin, epgRefreshHours = o.epgRefreshHours ?: epgRefreshHours,
        transcodeHw = o.transcodeHw ?: transcodeHw, proxyMode = o.proxyMode ?: proxyMode,
    )
}
