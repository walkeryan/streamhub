package com.streamhub.tv.feature.home

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.NativeDiscoveryFilters
import com.streamhub.tv.core.ui.components.*
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import java.time.Year

/** Source/language choices scope badges, never remove metadata titles from Discover. */
@Composable
fun DiscoveryScreen(
    onSearch: () -> Unit,
    onLibrary: () -> Unit,
    onSection: (String) -> Unit = {},
    vm: DiscoveryViewModel = hiltViewModel(),
) {
    val state by vm.state.collectAsStateWithLifecycle()
    val tokens = LocalThemeTokens.current
    val launcher = LocalPlayLauncher.current
    val rail = LocalRailController.current
    val grid = rememberLazyGridState()
    val searchFocus = remember { FocusRequester() }
    val controlFocus = remember { listOf("source", "language", "year", "sort").associateWith { FocusRequester() } }
    val tileFocus = remember { mutableMapOf<String, FocusRequester>() }
    var picker by remember { mutableStateOf<String?>(null) }
    var restoreTile by remember { mutableStateOf(vm.focusedId != null) }
    var restoreControl by remember { mutableStateOf<String?>(null) }
    var initialFocus by remember { mutableStateOf(true) }

    LaunchedEffect(state.scopeRevision) {
        picker = null; restoreControl = null
        if (!state.ready) { tileFocus.clear(); restoreTile = false; initialFocus = true }
    }
    LaunchedEffect(state.ready) {
        if (state.ready && initialFocus && !restoreTile) {
            withFrameNanos { }
            if (runCatching { searchFocus.requestFocus() }.getOrDefault(false)) initialFocus = false
        }
    }
    LaunchedEffect(state.page, state.filters) { if (!restoreTile) grid.scrollToItem(0) }
    LaunchedEffect(state.data) {
        val tiles = state.data?.tiles ?: return@LaunchedEffect
        tileFocus.keys.retainAll(tiles.map { it.id }.toSet())
        if (restoreTile) {
            val index = tiles.indexOfFirst { it.id == vm.focusedId }
            if (index >= 0) {
                grid.scrollToItem(index); withFrameNanos { }
                runCatching { tileFocus[vm.focusedId]?.requestFocus() }
            } else runCatching { searchFocus.requestFocus() }
            restoreTile = false; initialFocus = false
        }
    }
    LaunchedEffect(picker, restoreControl) {
        if (picker == null && restoreControl != null) {
            withFrameNanos { }
            runCatching { controlFocus[restoreControl]?.requestFocus() }
            restoreControl = null
        }
    }
    val sourceLabel = state.sources.find { it.id == state.filters.source }?.name ?: state.filters.source ?: "All sources"
    val languageLabel = state.languages.find { it.code == state.filters.language }?.name ?: when (state.filters.language) {
        null -> "My languages"; "all" -> "All languages"; "und" -> "Unknown audio"; else -> state.filters.language.orEmpty()
    }
    val sortLabel = when (state.filters.sort) { "rating" -> "Top rated"; "newest" -> "New releases"; else -> "Popular" }
    Column(
        Modifier.fillMaxSize().padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = Tokens.Canvas.SafeMarginY),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            ShButton("Search movies & series", onSearch, Modifier.weight(1f).focusRequester(searchFocus).leftOpensRail(rail))
            ShChip("Movies", state.section == "movies", { if (state.section != "movies") onSection("movies") })
            ShChip("Series", state.section == "shows", { if (state.section != "shows") onSection("shows") })
            ShButton("My library", onLibrary)
        }
        Text(if (state.section == "movies") "Discover movies" else "Discover series", style = MaterialTheme.typography.headlineMedium, color = tokens.text)
        Text("Source and audio language filters change availability, not the titles discovered.", color = tokens.textMuted)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShButton("Source: $sourceLabel", { picker = "source" }, Modifier.weight(1f).focusRequester(controlFocus.getValue("source")).leftOpensRail(rail), enabled = state.ready)
            ShButton(languageLabel, { picker = "language" }, Modifier.weight(1f).focusRequester(controlFocus.getValue("language")), enabled = state.ready)
            ShButton(state.filters.year?.toString() ?: "All years", { picker = "year" }, Modifier.focusRequester(controlFocus.getValue("year")), enabled = state.ready)
            ShButton(sortLabel, { picker = "sort" }, Modifier.focusRequester(controlFocus.getValue("sort")), enabled = state.ready)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            // Keep page controls focusable at either boundary so refresh never strands D-pad focus.
            ShButton(if (state.page > 1) "Previous" else "Previous · Start", { if (!state.loading && state.page > 1) vm.refresh(state.page - 1) }, Modifier.leftOpensRail(rail), enabled = state.ready)
            Text("Page ${state.page} · 20 per page" + (state.data?.total?.let { " · $it metadata titles" } ?: ""), color = tokens.text)
            ShButton(if (state.data?.hasMore == true && state.page < 500) "Next" else "Next · End", { if (!state.loading && state.data?.hasMore == true && state.page < 500) vm.refresh(state.page + 1) }, enabled = state.ready)
            ShButton("Reset filters", { vm.filters(NativeDiscoveryFilters()) }, enabled = state.ready)
            ShButton(if (state.loading) "Refreshing…" else "Refresh", { if (!state.loading) vm.refresh() }, enabled = state.ready)
        }
        state.notice?.let { Text(it, color = tokens.textMuted) }
        state.data?.coverageNote?.let { Text(it, color = tokens.textMuted) }
        Box(Modifier.weight(1f)) {
            when {
                !state.ready -> CenterMessage("Choose a profile", "Discover uses this profile's source availability and content settings.")
                state.loading -> LoadingView()
                state.error != null -> CenterMessage("Discovery unavailable", state.error) {
                    ShButton("Retry", { vm.refresh() }); ShButton("Browse my library", onLibrary)
                }
                state.data?.tiles.isNullOrEmpty() -> CenterMessage("No titles on this page", "Try the next page, change year or sort, or browse your library. Classification rules can leave sparse pages.")
                else -> LazyVerticalGrid(
                    columns = GridCells.Fixed(5), state = grid, modifier = Modifier.fillMaxSize().focusRestorer(),
                    contentPadding = PaddingValues(12.dp), horizontalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap), verticalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap),
                ) {
                    itemsIndexed(state.data!!.tiles, key = { _, tile -> tile.id }) { index, tile ->
                        PosterTile(tile, onClick = { vm.focusedId = tile.id; launcher.openDetails(tile.id) },
                            modifier = Modifier.focusRequester(tileFocus.getOrPut(tile.id) { FocusRequester() }).then(if (index % 5 == 0) Modifier.leftOpensRail(rail) else Modifier),
                            onFocus = { vm.focusedId = it.id })
                    }
                }
            }
        }
    }
    picker?.let { field ->
        val options = when (field) {
            "source" -> listOf(FilterOption(null, "All sources")) + state.sources.map { FilterOption(it.id, it.name) }
            "language" -> listOf(FilterOption(null, "My languages"), FilterOption("all", "All languages")) + state.languages.map { FilterOption(it.code, if (it.code == "und") "Unknown audio" else it.name) }
            "year" -> listOf(FilterOption(null, "All years")) + (Year.now().value.coerceIn(1888, 2100) downTo 1888).map { FilterOption(it.toString(), it.toString()) }
            else -> listOf(FilterOption("popular", "Popular"), FilterOption("rating", "Top rated"), FilterOption("newest", "New releases"))
        }
        val selected = when (field) { "source" -> state.filters.source; "language" -> state.filters.language; "year" -> state.filters.year?.toString(); else -> state.filters.sort }
        CatalogFilterPicker("Choose $field", selected, options, { value ->
            vm.filters(when (field) {
                "source" -> state.filters.copy(source = value)
                "language" -> state.filters.copy(language = value)
                "year" -> state.filters.copy(year = value?.toIntOrNull())
                else -> state.filters.copy(sort = value ?: "popular")
            })
        }, { restoreControl = field; picker = null })
    }
}
