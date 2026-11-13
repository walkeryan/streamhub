package com.streamhub.tv.feature.search

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.repo.LocalPrefs
import com.streamhub.tv.core.data.repo.ProgrammeRecorder
import com.streamhub.tv.core.data.repo.RecordingsRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * @param loading nothing of this search is on screen yet
 * @param discovering the hub's results are shown and the metadata provider's titles may still arrive
 * @param growing more rows of a section are being fetched
 */
internal data class SearchUi(
    val loading: Boolean = false,
    val view: SearchView? = null,
    val discovering: Boolean = false,
    val growing: Boolean = false,
    val error: String? = null,
)

@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class SearchViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val prefs: LocalPrefs,
    private val session: SessionStore,
    private val saved: SavedStateHandle,
    recordings: RecordingsRepository,
) : ViewModel() {
    // In SavedStateHandle so the search is still there after the system reclaims the app in the
    // background (a TV does this as soon as another app plays video).
    val query: StateFlow<String> = saved.getStateFlow(KEY_QUERY, "")
    /** The sections asked for in full, with the query they were asked for: a new query starts capped. */
    private val expanded = MutableStateFlow("" to emptySet<SearchSectionName>())
    private val scope = session.session.map { SearchScope(it.serverUrl, it.profile?.id, it.profile?.languages.orEmpty()) }.distinctUntilChanged()
    private val requests = combine(scope, query, expanded) { s, q, e -> SearchRequest(s, q.trim(), if (e.first == q.trim()) e.second else emptySet()) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)
    // Every query is asked twice: what the hub holds is shown as soon as it lands, and the titles
    // the metadata provider adds join the end of Movies & Shows when they arrive.
    private val loads = searchLoads(
        requests,
        local = { request -> api.search(request.query, discover = false, expand = request.expand.map { it.wire }) },
        full = { request -> api.search(request.query, discover = true, expand = request.expand.filter { it == SearchSectionName.TITLES }.map { it.wire }) },
    )
    val recent: StateFlow<List<String>> = session.session.map { it.serverUrl to it.profile?.id }.distinctUntilChanged()
        .flatMapLatest { (server,id) -> if (server == null || id == null) flowOf(emptyList()) else prefs.recentSearches(server,id) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    /** Recording states of broadcasts scheduled from this screen, by programme id. */
    private val _recorded = MutableStateFlow<Map<String, String?>>(emptyMap())
    val recorded: StateFlow<Map<String, String?>> = _recorded.asStateFlow()

    /** The Record actions of a broadcast that is still to come. */
    val recorder = ProgrammeRecorder(recordings, viewModelScope) { programme: Programme, state -> _recorded.update { it + (programme.id to state) } }

    internal val ui: StateFlow<SearchUi> = combine(requests, loads) { request, loaded ->
        // An answer to an older request is never shown: until this one's first word, the page is loading.
        if (request != loaded.request) {
            val kept = loaded.view?.takeIf { request != null && request.sameSearch(loaded.request) }
            SearchUi(loading = kept == null && request != null && request.query.length >= 2 && request.scope.profileId != null, view = kept, growing = kept != null)
        } else SearchUi(loaded.loading, loaded.view, loaded.discovering, loaded.growing, loaded.error?.let { it.message ?: "Search failed" })
    }.stateIn(viewModelScope, SharingStarted.Eagerly, SearchUi())

    fun onQuery(q: String) { saved[KEY_QUERY] = q }

    /** "Show more" on a row: ask the server for the rest of that section. What is shown stays where it is. */
    internal fun showMore(section: SearchSectionName) {
        val q = query.value.trim()
        expanded.update { (asked, sections) -> q to ((if (asked == q) sections else emptySet()) + section) }
    }

    fun commit() {
        val q = query.value.trim()
        val owner = requests.value?.scope ?: return
        val profileId = owner.profileId ?: return
        val server = owner.server ?: return
        if (q.length < 2) return
        viewModelScope.launch { prefs.pushRecentSearch(server, profileId, q) }
    }

    private companion object {
        const val KEY_QUERY = "query"
    }
}
