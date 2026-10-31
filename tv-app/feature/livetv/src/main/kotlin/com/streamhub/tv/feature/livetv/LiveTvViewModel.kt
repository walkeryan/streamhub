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
import com.streamhub.tv.core.data.repo.PlaybackCoordinator
import com.streamhub.tv.core.player.PlayerFactory
import com.streamhub.tv.core.player.StreamHubPlayer
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
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
    val selectedGroup: String? = null,
    val favoritesOnly: Boolean = false,
    val channels: List<Channel> = emptyList(),
    val listId: String = "",
    val previewChannelId: String? = null,
)

@HiltViewModel
class LiveTvViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val cache: ChannelListCache,
    private val session: SessionStore,
    private val playback: PlaybackCoordinator,
    playerFactory: PlayerFactory,
) : ViewModel() {
    private val _state = MutableStateFlow(LiveTvState())
    val state: StateFlow<LiveTvState> = _state.asStateFlow()

    /** Muted-by-default preview player (no progress reporting). */
    val preview: StreamHubPlayer = playerFactory.create(reportProgress = false)
    private var previewJob: Job? = null
    private var loadJob: Job? = null

    init {
        viewModelScope.launch {
            val profileLangs = session.session.first().profile?.languages.orEmpty()
            _state.update { it.copy(profileLanguages = profileLangs, selectedLanguages = profileLangs.toSet().ifEmpty { null }) }
            launch { runCatching { api.channelLanguages() }.onSuccess { l -> _state.update { it.copy(languages = l.sortedByDescending { x -> x.count }) } } }
            launch { runCatching { api.channelGroups() }.onSuccess { g -> _state.update { it.copy(groups = g.filterNot { x -> x.hidden }) } } }
            load()
        }
    }

    fun load() {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            val s = _state.value
            _state.update { it.copy(loading = true, error = null) }
            // "All" must send something so the server does not apply profile defaults: empty csv = all.
            val langs = s.selectedLanguages?.toList() ?: emptyList()
            runCatching { cache.fetch(group = s.selectedGroup, languages = langs, favorites = s.favoritesOnly.takeIf { it }) }
                .onSuccess { l -> _state.update { it.copy(loading = false, channels = l.channels, listId = l.listId) } }
                .onFailure { e -> _state.update { it.copy(loading = false, error = e.message) } }
        }
    }

    fun toggleLanguage(code: String?) {
        _state.update {
            if (code == null) it.copy(selectedLanguages = null)
            else {
                val cur = it.selectedLanguages.orEmpty()
                val next = if (code in cur) cur - code else cur + code
                it.copy(selectedLanguages = next.ifEmpty { null })
            }
        }
        load()
    }

    fun selectGroup(name: String?) { _state.update { it.copy(selectedGroup = name) }; load() }
    fun toggleFavoritesOnly() { _state.update { it.copy(favoritesOnly = !it.favoritesOnly) }; load() }

    /** BRIEF: preview plays the focused channel after 600 ms dwell. */
    fun onChannelFocused(ch: Channel) {
        previewJob?.cancel()
        previewJob = viewModelScope.launch {
            delay(600)
            if (_state.value.previewChannelId == ch.id) return@launch
            runCatching { playback.resolveDirect(ch.id) }.onSuccess { d ->
                if (d.url != null) {
                    preview.play(d)
                    preview.exoPlayer.volume = 0.6f
                    _state.update { it.copy(previewChannelId = ch.id) }
                }
            }
        }
    }

    fun toggleFavorite(ch: Channel) {
        viewModelScope.launch {
            runCatching {
                if (ch.isFavorite) api.removeFavorite(MediaKind.CHANNEL, ch.id) else api.addFavorite(Favorite(MediaKind.CHANNEL, ch.id))
            }.onSuccess {
                _state.update { s -> s.copy(channels = s.channels.map { if (it.id == ch.id) it.copy(isFavorite = !ch.isFavorite) else it }) }
            }
        }
    }

    fun stopPreview() { previewJob?.cancel(); preview.stop(); _state.update { it.copy(previewChannelId = null) } }

    override fun onCleared() { preview.release() }
}
