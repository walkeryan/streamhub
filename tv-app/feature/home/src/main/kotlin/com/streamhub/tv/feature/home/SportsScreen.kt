package com.streamhub.tv.feature.home

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.ProfileFilterStore
import com.streamhub.tv.core.data.repo.CatalogScope
import com.streamhub.tv.core.data.repo.catalogScope
import com.streamhub.tv.core.ui.components.*
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import javax.inject.Inject

data class SportsState(
    val filters: NativeSportsFilters = NativeSportsFilters(), val result: SportsPage = SportsPage(),
    val languages: List<LanguageCount> = emptyList(), val page: Int = 0,
    val loading: Boolean = true, val ready: Boolean = false, val error: String? = null, val notice: String? = null,
)

@HiltViewModel
class SportsViewModel @Inject constructor(private val api: StreamHubApi, private val session: SessionStore, private val prefs: ProfileFilterStore) : ViewModel() {
    private val _state = MutableStateFlow(SportsState())
    val state: StateFlow<SportsState> = _state.asStateFlow()
    private var scope: CatalogScope? = null
    private suspend fun ensureScope(expected: CatalogScope) {
        if (scope != expected || session.current().catalogScope() != expected) throw CancellationException("Catalog scope changed")
    }
    private var request: Job? = null
    init {
        viewModelScope.launch {
            session.session.map { it.catalogScope() }.distinctUntilChanged().collectLatest { owner ->
                val id = owner.profileId
                request?.cancel(); scope = owner; _state.value = SportsState()
                if (id != null) {
                    try {
                        val saved = prefs.read(id, "native_sports")?.let { StreamHubJson.decodeFromJsonElement<NativeSportsFilters>(it) } ?: NativeSportsFilters()
                        ensureScope(owner)
                        _state.update { it.copy(filters = saved) }
                    } catch (e: CancellationException) { throw e }
                    catch (_: Exception) { ensureScope(owner); _state.update { it.copy(notice = "Saved filters unavailable. Using defaults.") } }
                    ensureScope(owner)
                    _state.update { it.copy(ready = true) }; refresh(0)
                }
            }
        }
    }
    fun filters(value: NativeSportsFilters) {
        if (!_state.value.ready) return
        _state.update { it.copy(filters = value, result = SportsPage()) }
        val owner = scope ?: return
        val id = owner.profileId ?: return
        viewModelScope.launch {
            try { ensureScope(owner); prefs.write(id, "native_sports", StreamHubJson.encodeToJsonElement(value)) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { if (scope == owner) _state.update { it.copy(notice = "Filters apply now, but could not be saved.") } }
        }
        refresh(0)
    }
    fun refresh(page: Int = _state.value.page) {
        if (!_state.value.ready) return
        val owner = scope ?: return
        val f = _state.value.filters
        request?.cancel()
        request = viewModelScope.launch {
            _state.update { it.copy(loading = true, page = page, error = null, result = if (it.page == page) it.result else SportsPage()) }
            try {
                val result = api.sports(f.language, f.sport, page * 40, 40)
                ensureScope(owner)
                _state.update { it.copy(result = result, loading = false) }
                try { val languages = api.channelLanguages(); ensureScope(owner); _state.update { it.copy(languages = languages) } }
                catch (e: CancellationException) { throw e }
                catch (_: Exception) { ensureScope(owner); _state.update { it.copy(notice = "Language choices could not refresh.") } }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { ensureScope(owner); _state.update { it.copy(loading = false, error = e.message ?: "Could not load sports") } }
        }
    }
}

@Composable
fun SportsScreen(vm: SportsViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val rail = LocalRailController.current
    val launcher = LocalPlayLauncher.current
    val list = rememberLazyListState()
    var picker by remember { mutableStateOf<String?>(null) }
    val firstControl = remember { FocusRequester() }
    var needsInitialFocus by rememberSaveable { mutableStateOf(true) }
    LaunchedEffect(s.ready) {
        if (!s.ready) { needsInitialFocus = true; picker = null }
        else if (needsInitialFocus) {
            withFrameNanos { }
            if (runCatching { firstControl.requestFocus() }.getOrDefault(false)) needsInitialFocus = false
        }
    }
    LaunchedEffect(s.page, s.filters) { list.scrollToItem(0) }
    // Only refresh while this destination is composed; never retain stale schedules indefinitely.
    LaunchedEffect(vm) { while (true) { delay(60_000); vm.refresh() } }
    Column(Modifier.fillMaxSize().padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = Tokens.Canvas.SafeMarginY), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Sports on now", style = MaterialTheme.typography.headlineMedium, color = t.text)
        Text("Current guide listings; may include replays and sports news. No scores or live-event claims.", color = t.textMuted)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShButton(s.filters.sport ?: "All sports", { picker = "sport" }, Modifier.focusRequester(firstControl).leftOpensRail(rail), enabled = s.ready)
            ShButton(s.languages.find { it.code == s.filters.language }?.name ?: when(s.filters.language) { null -> "My languages"; "all" -> "All languages"; "und" -> "Unknown"; else -> s.filters.language!! }, { picker = "language" }, enabled = s.ready)
            ShButton(if (s.loading) "Refreshing…" else "Refresh", { if (s.ready && !s.loading) vm.refresh() }, enabled = s.ready)
            ShButton("Reset filters", { vm.filters(NativeSportsFilters()) }, enabled = s.ready)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShButton(if (s.page > 0) "Previous" else "Previous · Start", { if (!s.loading && s.page > 0) vm.refresh(s.page - 1) }, Modifier.leftOpensRail(rail), enabled = s.ready)
            Text("Page ${s.page + 1} · ${s.result.total} listings${if (s.loading) " · Loading…" else ""}", color = t.text)
            ShButton(if ((s.page + 1) * 40 < s.result.total) "Next" else "Next · End", { if (!s.loading && (s.page + 1) * 40 < s.result.total) vm.refresh(s.page + 1) }, enabled = s.ready)
        }
        Text("Guide coverage: ${s.result.coverage.scheduledChannels} of ${s.result.coverage.eligibleChannels} eligible channels", color = t.textMuted)
        s.notice?.let { Text(it, color = t.textMuted) }
        when {
            s.loading && s.result.items.isEmpty() -> LoadingView()
            s.error != null -> CenterMessage("Couldn't load sports", s.error) { ShButton("Retry", { vm.refresh() }) }
            s.result.items.isEmpty() -> CenterMessage("No matching sports listings", "Try All sports or All languages. Missing guide listings do not mean no sports are airing.")
            else -> LazyColumn(state = list, modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(8.dp)) {
                items(s.result.items, key = { it.id }) { event ->
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("${event.sport} · ${event.sourceName}", color = t.textMuted)
                        ShButton("Watch ${event.channelName} · ${event.title}", { launcher.play(event.channelId) }, Modifier.fillMaxWidth().leftOpensRail(rail))
                    }
                }
            }
        }
    }
    picker?.let { field ->
        val options = if (field == "sport") listOf(FilterOption(null, "All sports")) + s.result.sports.map { FilterOption(it.name, "${it.name} · ${it.count}") }
        else listOf(FilterOption(null, "My languages"), FilterOption("all", "All languages")) + s.languages.map { FilterOption(it.code, "${if(it.code == "und") "Unknown" else it.name} · ${it.count}") }
        CatalogFilterPicker("Choose $field", if(field == "sport") s.filters.sport else s.filters.language, options, { value -> vm.filters(if(field == "sport") s.filters.copy(sport = value) else s.filters.copy(language = value)) }, { picker = null })
    }
}
