package com.streamhub.tv.feature.search

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.SearchResponse
import com.streamhub.tv.core.data.repo.LocalPrefs
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SearchUi(val loading: Boolean = false, val result: SearchResponse? = null, val error: String? = null)

@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class SearchViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val prefs: LocalPrefs,
    private val session: SessionStore,
) : ViewModel() {
    val query = MutableStateFlow("")
    val language = MutableStateFlow<String?>(null)
    private val scope = session.session.map { SearchScope(it.serverUrl, it.profile?.id, it.profile?.languages.orEmpty()) }.distinctUntilChanged()
    private val requests = combine(scope, query, language) { s, q, l -> SearchRequest(s, q.trim(), l) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)
    private val loads = searchLoads(requests) { request ->
        api.search(request.query, languages = request.language?.let { if (it == "all") emptyList() else listOf(it) })
    }
    val recent: StateFlow<List<String>> = session.session.map { it.profile?.id }.distinctUntilChanged()
        .flatMapLatest { id -> if (id == null) flowOf(emptyList()) else prefs.recentSearches(id) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    val ui: StateFlow<SearchUi> = combine(requests, loads) { request, loaded ->
        if (request != loaded.request) SearchUi(loading = request != null && request.query.length >= 2 && request.scope.profileId != null)
        else SearchUi(loaded.loading, loaded.result?.getOrNull(), loaded.result?.exceptionOrNull()?.message)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, SearchUi())

    fun onQuery(q: String) { query.value = q }
    fun onLanguage(value: String?) { language.value = value }
    fun commit() {
        val q = query.value.trim()
        val profileId = requests.value?.scope?.profileId ?: return
        if (q.length < 2) return
        viewModelScope.launch { prefs.pushRecentSearch(profileId, q) }
    }
}
