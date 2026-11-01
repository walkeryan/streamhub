package com.streamhub.tv.feature.home

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.SavedStateHandle
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
import com.streamhub.tv.core.ui.components.*
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import javax.inject.Inject

data class BrowseState(
    val section: String, val filters: NativeBrowseFilters = NativeBrowseFilters(),
    val tiles: List<Tile> = emptyList(), val sources: List<Source> = emptyList(),
    val genres: List<CatalogFacet> = emptyList(), val languages: List<LanguageCount> = emptyList(),
    val page: Int = 1, val total: Int = 0, val loading: Boolean = true,
    val error: String? = null, val notice: String? = null, val ready: Boolean = false,
)

@HiltViewModel
class BrowseViewModel @Inject constructor(
    private val api: StreamHubApi, session: SessionStore, private val prefs: ProfileFilterStore, saved: SavedStateHandle,
) : ViewModel() {
    private val section: String = checkNotNull(saved["section"])
    private val _state = MutableStateFlow(BrowseState(section))
    val state: StateFlow<BrowseState> = _state.asStateFlow()
    private var profile: String? = null
    private var load: Job? = null
    private val prefKey = "native_browse_$section"
    init {
        viewModelScope.launch {
            session.session.map { it.profile?.id }.distinctUntilChanged().collectLatest { id ->
                load?.cancel(); profile = id; _state.value = BrowseState(section)
                if (id != null) {
                    try {
                        val savedFilters = prefs.read(id, prefKey)?.let { StreamHubJson.decodeFromJsonElement<NativeBrowseFilters>(it) } ?: NativeBrowseFilters()
                        _state.update { it.copy(filters = savedFilters) }
                    } catch (e: CancellationException) { throw e }
                    catch (_: Exception) { _state.update { it.copy(notice = "Saved filters unavailable. Using defaults.") } }
                    _state.update { it.copy(ready = true) }; refresh(1)
                }
            }
        }
    }
    fun filters(value: NativeBrowseFilters) {
        if (!_state.value.ready) return
        _state.update { it.copy(filters = value) }
        val id = profile ?: return
        viewModelScope.launch {
            try { prefs.write(id, prefKey, StreamHubJson.encodeToJsonElement(value)) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { if (profile == id) _state.update { it.copy(notice = "Filters apply now, but could not be saved. Try again.") } }
        }
        refresh(1)
    }
    fun refresh(page: Int = _state.value.page) {
        val id = profile ?: return
        val filters = _state.value.filters
        load?.cancel()
        load = viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null, tiles = emptyList(), page = page) }
            try {
                val result = api.browse(section, filters.source, filters.genre, filters.sort, page, filters.language)
                if (profile != id) return@launch
                _state.update { it.copy(tiles = result.tiles, page = result.page, total = result.total, loading = false) }
                // Metadata errors do not discard a successful page.
                try {
                    val sources = api.sources().filter { it.enabled && it.type != SourceType.STREAMING_SERVICE }
                    val genres = api.browseGenres(section, filters.source, filters.language)
                    val languages = api.browseLanguages(section, filters.source)
                    _state.update { it.copy(sources = sources, genres = genres, languages = languages) }
                } catch (e: CancellationException) { throw e }
                catch (_: Exception) { _state.update { it.copy(notice = "Some filter choices could not refresh. Retry to update them.") } }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(loading = false, error = e.message ?: "Could not load library") } }
        }
    }
}

@Composable
fun BrowseScreen(title: String, vm: BrowseViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val launcher = LocalPlayLauncher.current
    val rail = LocalRailController.current
    val grid = rememberLazyGridState()
    var picker by remember { mutableStateOf<String?>(null) }
    val firstControl = remember { FocusRequester() }
    var needsInitialFocus by rememberSaveable { mutableStateOf(true) }
    LaunchedEffect(s.ready) {
        if (s.ready && needsInitialFocus) {
            runCatching { firstControl.requestFocus() }.onSuccess { needsInitialFocus = false }
        }
    }
    LaunchedEffect(s.page, s.filters) { grid.scrollToItem(0) }
    val sourceName = s.sources.find { it.id == s.filters.source }?.name ?: s.filters.source ?: "All sources"
    val languageName = s.languages.find { it.code == s.filters.language }?.name ?: when(s.filters.language) { null -> "My languages"; "all" -> "All languages"; "und" -> "Unknown"; else -> s.filters.language!! }
    Column(Modifier.fillMaxSize().padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = Tokens.Canvas.SafeMarginY), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("$title · ${s.total} titles", style = MaterialTheme.typography.headlineMedium, color = t.text)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShButton("Source: $sourceName", { picker = "source" }, Modifier.weight(1f).focusRequester(firstControl).leftOpensRail(rail), enabled = s.ready)
            ShButton("Genre: ${s.filters.genre ?: "All genres"}", { picker = "genre" }, Modifier.weight(1f), enabled = s.ready)
            ShButton(languageName, { picker = "language" }, Modifier.weight(1f), enabled = s.ready)
            ShButton("Sort", { picker = "sort" }, enabled = s.ready)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShButton("Previous", { vm.refresh(s.page - 1) }, Modifier.leftOpensRail(rail), enabled = !s.loading && s.page > 1)
            Text("Page ${s.page}", color = t.text)
            ShButton("Next", { vm.refresh(s.page + 1) }, enabled = !s.loading && s.page * 60 < s.total)
            ShButton("Reset filters", { vm.filters(NativeBrowseFilters()) }, enabled = s.ready)
            ShButton("Refresh", { vm.refresh() }, enabled = s.ready && !s.loading)
        }
        s.notice?.let { Text(it, color = t.textMuted) }
        when {
            s.loading -> LoadingView()
            s.error != null -> CenterMessage("Couldn't load $title", s.error) { ShButton("Retry", { vm.refresh() }) }
            s.tiles.isEmpty() -> CenterMessage("No matching titles", "Change a filter or choose Reset filters. Other languages remain available.")
            else -> LazyVerticalGrid(columns = GridCells.Fixed(5), state = grid, modifier = Modifier.weight(1f).focusRestorer(), contentPadding = PaddingValues(12.dp), horizontalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap), verticalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap)) {
                itemsIndexed(s.tiles, key = { _, tile -> tile.id }) { i, tile ->
                    PosterTile(tile, onClick = { launcher.openDetails(tile.id) }, modifier = if (i % 5 == 0) Modifier.leftOpensRail(rail) else Modifier)
                }
            }
        }
    }
    picker?.let { field ->
        val options = when(field) {
            "source" -> listOf(FilterOption(null, "All sources")) + s.sources.map { FilterOption(it.id, it.name) }
            "genre" -> listOf(FilterOption(null, "All genres")) + s.genres.map { FilterOption(it.name, "${it.name} · ${it.count}") }
            "language" -> listOf(FilterOption(null, "My languages"), FilterOption("all", "All languages")) + s.languages.map { FilterOption(it.code, "${if(it.code == "und") "Unknown" else it.name} · ${it.count}") }
            else -> listOf(FilterOption("added", "Recently added"), FilterOption("title", "A–Z"), FilterOption("year", "Year"), FilterOption("rating", "Rating"))
        }
        val selected = when(field) { "source" -> s.filters.source; "genre" -> s.filters.genre; "language" -> s.filters.language; else -> s.filters.sort }
        CatalogFilterPicker("Choose $field", selected, options, { value -> vm.filters(when(field) { "source" -> s.filters.copy(source = value); "genre" -> s.filters.copy(genre = value); "language" -> s.filters.copy(language = value); else -> s.filters.copy(sort = value ?: "added") }) }, { picker = null })
    }
}
