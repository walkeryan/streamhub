package com.streamhub.tv.feature.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.snapshotFlow
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
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.CenterMessage
import com.streamhub.tv.core.ui.components.LoadingView
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.PosterTile
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class BrowseState(
    val section: String,
    val sort: String = "added",
    val tiles: List<Tile> = emptyList(),
    val page: Int = 0,
    val total: Int = 0,
    val loading: Boolean = true,
    val error: String? = null,
)

/** Movies / Shows: paged poster grid from GET /browse/{section}. */
@HiltViewModel
class BrowseViewModel @Inject constructor(private val api: StreamHubApi, saved: SavedStateHandle) : ViewModel() {
    private val _state = MutableStateFlow(BrowseState(section = checkNotNull(saved["section"])))
    val state: StateFlow<BrowseState> = _state.asStateFlow()

    init { loadMore() }

    fun setSort(sort: String) {
        _state.update { BrowseState(section = it.section, sort = sort) }
        loadMore()
    }

    fun loadMore() {
        val s = _state.value
        if (s.page > 0 && s.tiles.size >= s.total) return
        viewModelScope.launch {
            _state.update { it.copy(loading = true) }
            runCatching { api.browse(s.section, sort = s.sort, page = s.page + 1) }
                .onSuccess { p -> _state.update { it.copy(tiles = it.tiles + p.tiles, page = p.page, total = p.total, loading = false) } }
                .onFailure { e -> _state.update { it.copy(loading = false, error = e.message) } }
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
    val columns = 5

    LaunchedEffect(grid) {
        snapshotFlow { grid.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }
            .collect { last -> if (last >= s.tiles.size - columns * 2 && !s.loading) vm.loadMore() }
    }

    when {
        s.tiles.isEmpty() && s.loading -> LoadingView()
        s.tiles.isEmpty() && s.error != null -> CenterMessage("Couldn't load $title", s.error) { ShButton("Retry", vm::loadMore, style = ButtonStyle.Primary) }
        s.tiles.isEmpty() -> CenterMessage("No $title yet", "Add a Plex, Jellyfin or IPTV source with a library.")
        else -> Column(Modifier.fillMaxSize().padding(top = Tokens.Canvas.SafeMarginY)) {
            Text(title, style = MaterialTheme.typography.headlineMedium, color = t.text, modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX))
            androidx.compose.foundation.layout.Row(Modifier.padding(start = Tokens.Canvas.SafeMarginX, top = 8.dp).focusRestorer(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("added" to "Recently added", "title" to "A–Z", "year" to "Year", "rating" to "Rating").forEachIndexed { i, (k, label) ->
                    ShChip(label, s.sort == k, { vm.setSort(k) }, if (i == 0) Modifier.leftOpensRail(rail) else Modifier)
                }
            }
            PivotScroll(Tokens.Canvas.SafeMarginY) {
                LazyVerticalGrid(
                    columns = GridCells.Fixed(columns),
                    state = grid,
                    modifier = Modifier.fillMaxSize().focusRestorer(),
                    contentPadding = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX, vertical = 16.dp),
                    horizontalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap),
                    verticalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap),
                ) {
                    itemsIndexed(s.tiles, key = { _, it -> it.id }) { i, tile ->
                        PosterTile(tile, onClick = { launcher.openDetails(tile.id) }, onLongClick = { launcher.play(tile.id) },
                            modifier = if (i % columns == 0) Modifier.leftOpensRail(rail) else Modifier)
                    }
                }
            }
        }
    }
}
