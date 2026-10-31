package com.streamhub.tv.feature.search

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.SearchResponse
import com.streamhub.tv.core.data.repo.LocalPrefs
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SearchUi(val loading: Boolean = false, val result: SearchResponse? = null, val error: String? = null)

@OptIn(FlowPreview::class, ExperimentalCoroutinesApi::class)
@HiltViewModel
class SearchViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val prefs: LocalPrefs,
    private val session: SessionStore,
) : ViewModel() {
    val query = MutableStateFlow("")

    val recent: StateFlow<List<String>> = session.session
        .flatMapLatest { prefs.recentSearches(it.profile?.id ?: "default") }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    val ui: StateFlow<SearchUi> = query
        .map { it.trim() }
        .debounce(400) // BRIEF: debounce 400 ms
        .distinctUntilChanged()
        .flatMapLatest { q ->
            flow {
                if (q.length < 2) { emit(SearchUi()); return@flow }
                emit(SearchUi(loading = true))
                emit(runCatching { api.search(q) }.fold({ SearchUi(result = it) }, { SearchUi(error = it.message) }))
            }
        }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), SearchUi())

    fun onQuery(q: String) { query.value = q }

    fun commit() {
        val q = query.value.trim()
        if (q.length < 2) return
        viewModelScope.launch { prefs.pushRecentSearch(session.session.first().profile?.id ?: "default", q) }
    }
}
