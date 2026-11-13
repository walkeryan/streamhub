package com.streamhub.tv.feature.home

import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed as gridItemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.ProfileFilterStore
import com.streamhub.tv.core.data.repo.CatalogScope
import com.streamhub.tv.core.data.repo.catalogScope
import com.streamhub.tv.core.ui.components.*
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.RailController
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import javax.inject.Inject

data class BrowseState(
    val section: String, val filters: NativeBrowseFilters = NativeBrowseFilters(),
    /** Category rows. null = plain grid: a "See all" route, or a catalog without categories. */
    val shelves: List<Shelf>? = null,
    val tiles: List<Tile> = emptyList(), val sources: List<Source> = emptyList(),
    val genres: List<CatalogFacet> = emptyList(), val languages: List<LanguageCount> = emptyList(),
    val page: Int = 1, val total: Int = 0, val hasMore: Boolean = false,
    val loading: Boolean = true, val loadingMore: Boolean = false,
    val error: String? = null, val notice: String? = null, val ready: Boolean = false,
)

/**
 * Movies / Shows. The section route opens as shelves, one row per provider category in server
 * order, each loading its first page when it scrolls into view. A shelf's "See all" opens this same
 * view model on a route that carries the category, where it is a grid that appends pages as the
 * viewer scrolls. A server or source without categories gets the grid straight away.
 */
@HiltViewModel
class BrowseViewModel @Inject constructor(
    private val api: StreamHubApi, private val session: SessionStore, private val prefs: ProfileFilterStore, saved: SavedStateHandle,
) : ViewModel() {
    private val section: String = checkNotNull(saved["section"])
    /** Set on the "See all" route: the grid is pinned to this category and never shows shelves. */
    val category: String? = saved.get<String>("category")?.takeIf { it.isNotEmpty() }
    private val _state = MutableStateFlow(BrowseState(section))
    val state: StateFlow<BrowseState> = _state.asStateFlow()
    private var scope: CatalogScope? = null
    private suspend fun ensureScope(expected: CatalogScope) {
        if (scope != expected || session.current().catalogScope() != expected) throw CancellationException("Catalog scope changed")
    }
    private var load: Job? = null
    private var more: Job? = null
    private val shelfJobs = mutableListOf<Job>()
    /** Rows scrolled into view together load a few at a time rather than all at once. */
    private val shelfGate = Semaphore(SHELF_CONCURRENCY)
    /** False once the server has answered 404 for the category list: older servers only have the grid. */
    private var shelvesSupported = category == null
    private val prefKey = "native_browse_$section"
    init {
        viewModelScope.launch {
            session.session.map { it.catalogScope() }.distinctUntilChanged().collectLatest { owner ->
                val id = owner.profileId
                cancelLoads(); scope = owner; _state.value = BrowseState(section, NativeBrowseFilters(category = category))
                if (id != null) {
                    try {
                        val savedFilters = prefs.read(id, prefKey)?.let { StreamHubJson.decodeFromJsonElement<NativeBrowseFilters>(it) } ?: NativeBrowseFilters()
                        ensureScope(owner)
                        // The category belongs to the route, not to the saved filters.
                        _state.update { it.copy(filters = savedFilters.copy(category = category)) }
                    } catch (e: CancellationException) { throw e }
                    catch (_: Exception) { ensureScope(owner); _state.update { it.copy(notice = "Saved filters unavailable. Using defaults.") } }
                    ensureScope(owner)
                    _state.update { it.copy(ready = true) }; refresh()
                }
            }
        }
    }
    private fun cancelLoads() {
        load?.cancel(); more?.cancel()
        shelfJobs.forEach { it.cancel() }; shelfJobs.clear()
    }
    fun filters(value: NativeBrowseFilters) {
        if (!_state.value.ready) return
        val applied = value.copy(category = category)
        _state.update { it.copy(filters = applied) }
        val owner = scope ?: return
        val id = owner.profileId ?: return
        viewModelScope.launch {
            try { ensureScope(owner); prefs.write(id, prefKey, StreamHubJson.encodeToJsonElement(applied.copy(category = null))) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { if (scope == owner) _state.update { it.copy(notice = "Filters apply now, but could not be saved. Try again.") } }
        }
        refresh()
    }
    /** Reloads from the first page: the category list where there is one, otherwise the grid. */
    fun refresh() {
        if (!_state.value.ready) return
        val owner = scope ?: return
        if (owner.profileId == null) return
        val filters = _state.value.filters
        cancelLoads()
        load = viewModelScope.launch {
            _state.update { it.copy(loading = true, loadingMore = false, error = null) }
            try {
                if (shelvesSupported) {
                    val categories = try { api.browseCategories(section, filters.source, filters.language) }
                        catch (e: CancellationException) { throw e }
                        // A server without the category list (404) still has the grid.
                        catch (e: ApiException) { if (e.status == 404) null else throw e }
                    ensureScope(owner)
                    if (categories == null) shelvesSupported = false
                    val shelves = shelvesOf(categories.orEmpty())
                    if (shelves.isNotEmpty()) {
                        _state.update { it.copy(shelves = shelves, tiles = emptyList(), page = 1, total = 0, hasMore = false, loading = false) }
                        loadFacets(owner, filters)
                        return@launch
                    }
                }
                val result = api.browse(section, filters.source, filters.genre, filters.sort, 1, filters.language, filters.category)
                ensureScope(owner)
                _state.update { it.copy(shelves = null, tiles = result.tiles.distinctBy { tile -> tile.id }, page = result.page, total = result.total, hasMore = result.more, loading = false) }
                loadFacets(owner, filters)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { ensureScope(owner); _state.update { it.copy(loading = false, error = ApiErrors.describe(e).body) } }
        }
    }
    /** Filter choices. A failure here never discards the page that did load. */
    private suspend fun loadFacets(owner: CatalogScope, filters: NativeBrowseFilters) {
        try {
            val sources = api.sources().filter { it.enabled && it.type != SourceType.STREAMING_SERVICE }
            val genres = api.browseGenres(section, filters.source, filters.language, filters.category)
            val languages = api.browseLanguages(section, filters.source)
            ensureScope(owner)
            _state.update { it.copy(sources = sources, genres = genres, languages = languages) }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { ensureScope(owner); _state.update { it.copy(notice = "Some filter choices could not refresh. Retry to update them.") } }
    }
    private fun updateShelf(name: String, change: (Shelf) -> Shelf) =
        _state.update { s -> s.copy(shelves = s.shelves?.map { if (it.name == name) change(it) else it }) }
    /** Called when a shelf scrolls into view (and by its Retry tile). Loads the category's first page once. */
    fun loadShelf(name: String) {
        val owner = scope ?: return
        val shelf = _state.value.shelves?.firstOrNull { it.name == name } ?: return
        if (shelf.loaded || shelf.loading) return
        val filters = _state.value.filters
        updateShelf(name) { it.copy(loading = true, failed = false) }
        shelfJobs += viewModelScope.launch {
            try {
                val page = shelfGate.withPermit { api.browse(section, filters.source, filters.genre, filters.sort, 1, filters.language, name) }
                ensureScope(owner)
                updateShelf(name) { it.withFirstPage(page) }
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { ensureScope(owner); updateShelf(name) { it.copy(loading = false, failed = true) } }
        }
    }
    /** Next grid page, appended. The tiles already shown stay where they are while it loads. */
    fun loadMore() {
        val s = _state.value
        if (!s.ready || s.shelves != null || s.loading || s.loadingMore || !s.hasMore) return
        val owner = scope ?: return
        val filters = s.filters
        val next = s.page + 1
        _state.update { it.copy(loadingMore = true) }
        more = viewModelScope.launch {
            try {
                val result = api.browse(section, filters.source, filters.genre, filters.sort, next, filters.language, filters.category)
                ensureScope(owner)
                // An empty page ends paging whatever the counters say, so a miscounted total cannot loop.
                _state.update { it.copy(tiles = appendTiles(it.tiles, result.tiles), page = next, total = result.total, hasMore = result.more && result.tiles.isNotEmpty(), loadingMore = false) }
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { ensureScope(owner); _state.update { it.copy(loadingMore = false, hasMore = false, notice = "Couldn't load more titles. Choose Refresh to try again.") } }
        }
    }

    private companion object { const val SHELF_CONCURRENCY = 3 }
}

/** Frames to wait for the tile that opened a title to be composed again before giving up on it. */
private const val RETURN_FOCUS_FRAMES = 12
/** A shelf flung past faster than this never asks the server for its tiles. */
private const val SHELF_LOAD_DELAY_MS = 150L
/** Grid tiles from the end at which the next page is requested (two rows of five). */
private const val LOAD_AHEAD = 10
private const val GRID_COLUMNS = 5
private val FILTER_FIELDS = listOf("source", "genre", "language", "sort")

@Composable
fun BrowseScreen(title: String, onSeeAll: (category: String) -> Unit = {}, vm: BrowseViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val launcher = LocalPlayLauncher.current
    val rail = LocalRailController.current
    val grid = rememberLazyGridState()
    val scope = rememberCoroutineScope()
    var picker by remember { mutableStateOf<String?>(null) }
    val filterButtons = remember { FILTER_FIELDS.associateWith { FocusRequester() } }
    val firstControl = filterButtons.getValue("source")
    val screenFocus = remember { FocusRequester() }
    val returnFocus = remember { FocusRequester() }
    // Saved with the back-stack entry: which tile opened Details or "See all", and whether this screen has been shown.
    var opened by rememberSaveable { mutableStateOf<String?>(null) }
    var entered by rememberSaveable { mutableStateOf(false) }
    // Per composition: set once this visit's focus has been placed, so a later refresh does not move it again.
    var placed by remember { mutableStateOf(false) }
    val shelves = s.shelves?.visible()
    val contentShown = s.ready && !s.loading && s.error == null

    LaunchedEffect(s.ready) { if (!s.ready) { picker = null; placed = false } }
    // First visit: the first filter button. Coming back: the tile that was opened, so Back always
    // lands where the viewer left; if that tile is gone, wherever this screen last had focus.
    LaunchedEffect(s.ready, contentShown) {
        if (!s.ready || placed) return@LaunchedEffect
        withFrameNanos { }
        if (!entered) {
            if (runCatching { firstControl.requestFocus() }.getOrDefault(false)) { entered = true; placed = true }
            return@LaunchedEffect
        }
        if (!contentShown) return@LaunchedEffect
        placed = true
        if (opened != null) {
            repeat(RETURN_FOCUS_FRAMES) {
                if (runCatching { returnFocus.requestFocus() }.getOrDefault(false)) return@LaunchedEffect
                withFrameNanos { }
            }
        }
        runCatching { screenFocus.requestFocus() }
    }
    // Back to the top when the filters change, but not when this screen is merely shown again.
    var shownFilters by remember { mutableStateOf(s.filters) }
    LaunchedEffect(s.filters) { if (shownFilters != s.filters) { shownFilters = s.filters; runCatching { grid.scrollToItem(0) } } }

    val sourceName = s.sources.find { it.id == s.filters.source }?.name ?: s.filters.source ?: "All sources"
    val languageName = s.languages.find { it.code == s.filters.language }?.name ?: when(s.filters.language) { null -> "My languages"; "all" -> "All languages"; "und" -> "Unknown"; else -> s.filters.language!! }
    val heading = when {
        vm.category != null -> "$title · ${vm.category}" + if (contentShown) " · ${s.total} titles" else ""
        shelves != null -> "$title · ${shelves.size} categories"
        else -> "$title · ${s.total} titles"
    }
    Column(
        Modifier.fillMaxSize().padding(vertical = Tokens.Canvas.SafeMarginY).focusRequester(screenFocus).focusRestorer().focusGroup(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(heading, style = MaterialTheme.typography.headlineMedium, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX))
        // The filters stay above shelves and grid alike.
        Row(Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShButton("Source: $sourceName", { picker = "source" }, Modifier.weight(1f).focusRequester(filterButtons.getValue("source")).leftOpensRail(rail), enabled = s.ready)
            ShButton("Genre: ${s.filters.genre ?: "All genres"}", { picker = "genre" }, Modifier.weight(1f).focusRequester(filterButtons.getValue("genre")), enabled = s.ready)
            ShButton(languageName, { picker = "language" }, Modifier.weight(1f).focusRequester(filterButtons.getValue("language")), enabled = s.ready)
            ShButton("Sort", { picker = "sort" }, Modifier.focusRequester(filterButtons.getValue("sort")), enabled = s.ready)
            ShButton("Reset filters", { vm.filters(NativeBrowseFilters()) }, enabled = s.ready)
            ShButton(if (s.loading) "Refreshing…" else "Refresh", { if (s.ready && !s.loading) vm.refresh() }, enabled = s.ready)
        }
        s.notice?.let { Text(it, color = t.textMuted, modifier = Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX)) }
        when {
            s.loading -> LoadingView()
            s.error != null -> CenterMessage("Couldn't load $title", s.error) { ShButton("Retry", { vm.refresh() }) }
            shelves != null -> if (shelves.isEmpty()) {
                CenterMessage("No matching titles", "Change a filter or choose Reset filters. Other languages remain available.")
            } else {
                PivotScroll(leadingOffset = 0.dp) {
                    LazyColumn(
                        state = rememberLazyListState(),
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                        contentPadding = PaddingValues(bottom = Tokens.Canvas.SafeMarginY),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        items(shelves, key = { it.name }) { shelf ->
                            ShelfRow(
                                shelf = shelf, rail = rail, opened = opened, returnFocus = returnFocus,
                                onLoad = { vm.loadShelf(shelf.name) },
                                onOpen = { tile -> opened = openedKey(shelf.name, tile.id); launcher.openDetails(tile.id) },
                                onSeeAll = { opened = openedKey(shelf.name, SEE_ALL_ID); onSeeAll(shelf.name) },
                            )
                        }
                    }
                }
            }
            s.tiles.isEmpty() -> CenterMessage("No matching titles", "Change a filter or choose Reset filters. Other languages remain available.")
            else -> LazyVerticalGrid(
                columns = GridCells.Fixed(GRID_COLUMNS), state = grid,
                modifier = Modifier.weight(1f).padding(horizontal = Tokens.Canvas.SafeMarginX),
                contentPadding = PaddingValues(12.dp),
                horizontalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap), verticalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap),
            ) {
                gridItemsIndexed(s.tiles, key = { _, tile -> tile.id }) { i, tile ->
                    // Composing a tile near the end means the viewer is close to it: fetch the next page now.
                    if (s.hasMore && i >= s.tiles.size - LOAD_AHEAD) LaunchedEffect(s.tiles.size) { vm.loadMore() }
                    PosterTile(
                        tile,
                        onClick = { opened = openedKey(null, tile.id); launcher.openDetails(tile.id) },
                        modifier = (if (i % GRID_COLUMNS == 0) Modifier.leftOpensRail(rail) else Modifier)
                            .then(if (opened == openedKey(null, tile.id)) Modifier.focusRequester(returnFocus) else Modifier),
                    )
                }
                if (s.loadingMore) item(key = "loading-more", span = { GridItemSpan(maxLineSpan) }) {
                    Text("Loading more…", color = t.textMuted, modifier = Modifier.padding(vertical = 12.dp))
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
        CatalogFilterPicker(
            "Choose $field", selected, options,
            { value -> vm.filters(when(field) { "source" -> s.filters.copy(source = value); "genre" -> s.filters.copy(genre = value); "language" -> s.filters.copy(language = value); else -> s.filters.copy(sort = value ?: "added") }) },
            {
                picker = null
                // The picker is its own window; put focus back on the button that opened it.
                scope.launch { withFrameNanos { }; runCatching { filterButtons[field]?.requestFocus() } }
            },
        )
    }
}

/** Height a shelf keeps while its tiles load, so rows below do not jump when they arrive. */
private val SHELF_ROW_HEIGHT = Tokens.Tile.PosterH + 64.dp

/**
 * One category row. It has no focusRestorer of its own: Up/Down between shelves then follows the
 * column the viewer is in rather than jumping to wherever that row was last left.
 */
@Composable
private fun ShelfRow(
    shelf: Shelf, rail: RailController, opened: String?, returnFocus: FocusRequester,
    onLoad: () -> Unit, onOpen: (Tile) -> Unit, onSeeAll: () -> Unit,
) {
    val t = LocalThemeTokens.current
    // Composed = scrolled into (or next to) view. The delay drops rows that are only passing through.
    LaunchedEffect(shelf.name, shelf.loaded, shelf.failed) {
        if (!shelf.loaded && !shelf.failed) { delay(SHELF_LOAD_DELAY_MS); onLoad() }
    }
    Column(Modifier.fillMaxWidth()) {
        SectionTitle(if (shelf.count > 0) "${shelf.name} · ${shelf.count}" else shelf.name, Modifier.padding(start = Tokens.Canvas.SafeMarginX, end = Tokens.Canvas.SafeMarginX, bottom = 4.dp))
        Box(Modifier.fillMaxWidth().height(SHELF_ROW_HEIGHT)) {
            when {
                shelf.failed -> ShButton(
                    "Couldn't load ${shelf.name} · Retry", onLoad,
                    Modifier.align(Alignment.CenterStart).padding(start = Tokens.Canvas.SafeMarginX).leftOpensRail(rail),
                )
                !shelf.loaded -> Text("Loading…", color = t.textMuted, modifier = Modifier.align(Alignment.CenterStart).padding(start = Tokens.Canvas.SafeMarginX))
                else -> PivotScroll(leadingOffset = Tokens.Canvas.SafeMarginX) {
                    LazyRow(
                        state = rememberLazyListState(),
                        modifier = Modifier.fillMaxWidth(),
                        contentPadding = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX, vertical = 12.dp),
                        horizontalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap),
                    ) {
                        itemsIndexed(shelf.tiles, key = { _, tile -> tile.id }) { i, tile ->
                            PosterTile(
                                tile,
                                onClick = { onOpen(tile) },
                                modifier = (if (i == 0) Modifier.leftOpensRail(rail) else Modifier)
                                    .then(if (opened == openedKey(shelf.name, tile.id)) Modifier.focusRequester(returnFocus) else Modifier),
                            )
                        }
                        if (shelf.hasMore) item(key = SEE_ALL_ID) {
                            SeeAllTile(shelf, onSeeAll, if (opened == openedKey(shelf.name, SEE_ALL_ID)) Modifier.focusRequester(returnFocus) else Modifier)
                        }
                    }
                }
            }
        }
    }
}

/** Last tile of a shelf: opens the grid filtered to that category. Same footprint as a poster. */
@Composable
private fun SeeAllTile(shelf: Shelf, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    FocusSurface(onClick = onClick, modifier = modifier.size(Tokens.Tile.PosterW, Tokens.Tile.PosterH), containerColor = t.surfaceAlt) {
        Column(Modifier.align(Alignment.Center).padding(12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text("See all", color = t.text, style = MaterialTheme.typography.titleMedium)
            Text(if (shelf.count > 0) "${shelf.count} in ${shelf.name}" else shelf.name, color = t.textMuted, style = MaterialTheme.typography.labelMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
    }
}
