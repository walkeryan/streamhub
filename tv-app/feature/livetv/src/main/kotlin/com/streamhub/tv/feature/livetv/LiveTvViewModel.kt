package com.streamhub.tv.feature.livetv

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.data.model.ChannelGroup
import com.streamhub.tv.core.data.model.Favorite
import com.streamhub.tv.core.data.model.LanguageCount
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.repo.ChannelListCache
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/** null language set = "All" (explicitly no filter). */
data class LiveTvState(
    val loading: Boolean = true,
    val error: String? = null,
    val languages: List<LanguageCount> = emptyList(),
    val profileLanguages: List<String> = emptyList(),
    val selectedLanguages: Set<String>? = null,
    val groups: List<ChannelGroup> = emptyList(),
    val groupsLoading: Boolean = true,
    val groupsError: String? = null,
    val selectedGroup: String? = null,
    val country: String = "US",
    val favoritesOnly: Boolean = false,
    val channels: List<Channel> = emptyList(),
    val listId: String = "",
    val offset: Int = 0,
    val total: Int = 0,
    val previewChannelId: String? = null,
)

@HiltViewModel
class LiveTvViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val cache: ChannelListCache,
    private val session: SessionStore,
) : ViewModel() {
    private val _state = MutableStateFlow(LiveTvState())
    val state: StateFlow<LiveTvState> = _state.asStateFlow()

    private var loadJob: Job? = null
    private var groupsJob: Job? = null

    init {
        viewModelScope.launch {
            val profileLangs = session.session.first().profile?.languages.orEmpty()
            _state.update { it.copy(profileLanguages = profileLangs, selectedLanguages = profileLangs.toSet().ifEmpty { null }) }
            launch { runCatching { api.channelLanguages() }.onSuccess { l -> _state.update { it.copy(languages = l.sortedByDescending { x -> x.count }) } } }
            refreshGroups()
            load()
        }
    }

    fun refreshGroups() {
        groupsJob?.cancel()
        _state.update { it.copy(groupsLoading = true, groupsError = null) }
        groupsJob = viewModelScope.launch {
            val s = _state.value
            try {
                val groups = api.channelGroups(languages = s.selectedLanguages?.toList() ?: emptyList(), favorites = s.favoritesOnly, country = s.country)
                _state.update { it.copy(groups = groups.filterNot { g -> g.hidden }, groupsLoading = false) }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(groupsLoading = false, groupsError = e.message ?: "Couldn't load groups") } }
        }
    }

    fun load(offset: Int = 0) {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            val s = _state.value
            _state.update { it.copy(loading = true, error = null, channels = emptyList(), previewChannelId = null, offset = offset) }
            // "All" must send something so the server does not apply profile defaults: empty csv = all.
            val langs = s.selectedLanguages?.toList() ?: emptyList()
            runCatching { cache.fetch(group = s.selectedGroup, languages = langs, favorites = s.favoritesOnly.takeIf { it }, country = s.country, limit = 60, offset = offset) }
                .onSuccess { l -> _state.update { it.copy(loading = false, channels = l.channels, listId = l.listId, total = l.total ?: l.channels.size) } }
                .onFailure { e ->
                    if (e is CancellationException) throw e
                    _state.update { it.copy(loading = false, error = e.message) }
                }
        }
    }

    fun nextPage() { val s = _state.value; if (!s.loading && s.offset + 60 < s.total) load(s.offset + 60) }
    fun previousPage() { val s = _state.value; if (!s.loading && s.offset > 0) load((s.offset - 60).coerceAtLeast(0)) }

    fun toggleLanguage(code: String?) {
        _state.update {
            if (code == null) it.copy(selectedLanguages = null)
            else {
                val cur = it.selectedLanguages.orEmpty()
                val next = if (code in cur) cur - code else cur + code
                it.copy(selectedLanguages = next.ifEmpty { null })
            }
        }
        refreshGroups()
        load()
    }

    fun selectGroup(name: String?) { _state.update { it.copy(selectedGroup = name) }; load() }
    fun toggleFavoritesOnly() { _state.update { it.copy(favoritesOnly = !it.favoritesOnly) }; refreshGroups(); load() }

    /** Focus only updates metadata; browsing must never consume a provider stream. */
    fun onChannelFocused(ch: Channel) {
        _state.update { it.copy(previewChannelId = ch.id) }
    }

    fun selectCountry(value: String) {
        if (value == _state.value.country) return
        _state.update { it.copy(country = value, selectedGroup = null) }
        refreshGroups()
        load()
    }

    fun toggleFavorite(ch: Channel) {
        viewModelScope.launch {
            runCatching {
                if (ch.isFavorite) api.removeFavorite(MediaKind.CHANNEL, ch.id) else api.addFavorite(Favorite(MediaKind.CHANNEL, ch.id))
            }.onSuccess {
                _state.update { s -> s.copy(channels = s.channels.map { if (it.id == ch.id) it.copy(isFavorite = !ch.isFavorite) else it }) }
                if (_state.value.favoritesOnly) { refreshGroups(); load() }
            }
        }
    }

    fun stopPreview() { _state.update { it.copy(previewChannelId = null) } }
}
