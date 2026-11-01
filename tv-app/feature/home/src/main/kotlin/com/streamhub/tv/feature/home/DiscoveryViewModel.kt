package com.streamhub.tv.feature.home

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.CatalogScope
import com.streamhub.tv.core.data.repo.ProfileFilterStore
import com.streamhub.tv.core.data.repo.catalogScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import javax.inject.Inject

internal data class DiscoveryOwner(val catalog: CatalogScope, val generation: String?, val paired: Boolean)
internal fun Session.discoveryOwner() = DiscoveryOwner(catalogScope(), generation, isPaired)
internal data class DiscoveryQuery(val owner: DiscoveryOwner, val section: String, val filters: NativeDiscoveryFilters, val page: Int, val revision: Long)
internal data class DiscoveryPayload(val page: DiscoveryPage, val sources: List<Source>, val languages: List<LanguageCount>, val notice: String? = null)
internal data class DiscoveryLoad(val query: DiscoveryQuery?, val result: Result<DiscoveryPayload>? = null)

/** Cancel superseded pages; a late non-cooperative fetch must not publish into another scope. */
internal fun discoveryLoads(queries: Flow<DiscoveryQuery?>, fetch: suspend (DiscoveryQuery) -> DiscoveryPayload): Flow<DiscoveryLoad> = channelFlow {
    queries.collectLatest { query ->
        send(DiscoveryLoad(query))
        if (query != null) {
            val result = try { Result.success(fetch(query)) }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { Result.failure(error) }
            currentCoroutineContext().ensureActive()
            send(DiscoveryLoad(query, result))
        }
    }
}

data class DiscoveryState(
    val section: String,
    val filters: NativeDiscoveryFilters = NativeDiscoveryFilters(),
    val data: DiscoveryPage? = null,
    val sources: List<Source> = emptyList(),
    val languages: List<LanguageCount> = emptyList(),
    val page: Int = 1,
    val ready: Boolean = false,
    val loading: Boolean = false,
    val error: String? = null,
    val notice: String? = null,
    val scopeRevision: Long = 0,
)

@HiltViewModel
class DiscoveryViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val session: SessionStore,
    private val prefs: ProfileFilterStore,
    saved: SavedStateHandle,
) : ViewModel() {
    val section: String = saved.get<String>("section").takeIf { it == "shows" } ?: "movies"
    private val mutable = MutableStateFlow(DiscoveryState(section))
    val state = mutable.asStateFlow()
    private val queries = MutableStateFlow<DiscoveryQuery?>(null)
    private var owner: DiscoveryOwner? = null
    private var revision = 0L
    private var saveJob: Job? = null
    private val prefKey = "native_discover_$section"
    var focusedId: String? = null

    private suspend fun ensureOwner(expected: DiscoveryOwner) {
        currentCoroutineContext().ensureActive()
        if (owner != expected || session.current().discoveryOwner() != expected) throw CancellationException("Discovery scope changed")
    }

    init {
        viewModelScope.launch {
            discoveryLoads(queries) { query ->
                ensureOwner(query.owner)
                val page = api.discover(query.section, query.filters, query.page)
                ensureOwner(query.owner)
                var sources = emptyList<Source>()
                var languages = emptyList<LanguageCount>()
                var notice: String? = null
                try {
                    sources = api.sources().filter { it.enabled }
                    ensureOwner(query.owner)
                    languages = api.browseLanguages(query.section, query.filters.source)
                    ensureOwner(query.owner)
                } catch (cancelled: CancellationException) { throw cancelled }
                catch (_: Exception) { ensureOwner(query.owner); notice = "Some availability filter choices could not refresh. Retry to update them." }
                DiscoveryPayload(page, sources, languages, notice)
            }.collect { load ->
                val query = load.query ?: return@collect
                if (queries.value != query || owner != query.owner || session.current().discoveryOwner() != query.owner) return@collect
                if (load.result == null) mutable.update { it.copy(data = null, loading = true, error = null, page = query.page) }
                else load.result.fold(
                    onSuccess = { payload -> mutable.update { it.copy(data = payload.page, page = payload.page.page, sources = payload.sources, languages = payload.languages, loading = false, error = null, notice = payload.notice ?: it.notice) } },
                    onFailure = { error -> mutable.update { it.copy(data = null, loading = false, error = error.message ?: "Discovery is unavailable. Your library remains available.") } },
                )
            }
        }
        viewModelScope.launch {
            session.session.map { it.discoveryOwner() }.distinctUntilChanged().collectLatest { next ->
                queries.value = null; saveJob?.cancel(); owner = next; focusedId = null
                mutable.value = DiscoveryState(section, scopeRevision = ++revision)
                val profile = next.catalog.profileId
                if (!next.paired || profile == null) return@collectLatest
                var filters = NativeDiscoveryFilters()
                var notice: String? = null
                try {
                    filters = prefs.read(profile, prefKey)?.let { StreamHubJson.decodeFromJsonElement<NativeDiscoveryFilters>(it).normalized() } ?: filters
                } catch (cancelled: CancellationException) { throw cancelled }
                catch (_: Exception) { notice = "Saved filters unavailable. Using defaults." }
                ensureOwner(next)
                mutable.update { it.copy(filters = filters, ready = true, notice = notice) }
                refresh(1)
            }
        }
    }

    fun filters(value: NativeDiscoveryFilters) {
        val expected = owner ?: return
        val profile = expected.catalog.profileId ?: return
        if (!mutable.value.ready) return
        val normalized = value.normalized()
        mutable.update { it.copy(filters = normalized, notice = null) }
        saveJob?.cancel()
        saveJob = viewModelScope.launch {
            try { ensureOwner(expected); prefs.write(profile, prefKey, StreamHubJson.encodeToJsonElement(normalized)); ensureOwner(expected) }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { ensureOwner(expected); mutable.update { it.copy(notice = "Filters apply now, but could not be saved. Try again.") } }
        }
        focusedId = null
        refresh(1)
    }

    fun refresh(page: Int = mutable.value.page) {
        val expected = owner ?: return
        if (!mutable.value.ready || !expected.paired || expected.catalog.profileId == null) return
        val bounded = page.coerceIn(1, 500)
        if (bounded != mutable.value.page) focusedId = null
        mutable.update { it.copy(data = null, loading = true, error = null, page = bounded) }
        queries.value = DiscoveryQuery(expected, section, mutable.value.filters, bounded, ++revision)
    }
}
