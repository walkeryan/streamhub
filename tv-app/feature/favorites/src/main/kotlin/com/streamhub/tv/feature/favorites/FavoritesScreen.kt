package com.streamhub.tv.feature.favorites

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.CenterMessage
import com.streamhub.tv.core.ui.components.ChannelTile
import com.streamhub.tv.core.ui.components.FocusRow
import com.streamhub.tv.core.ui.components.LoadingView
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.PosterTile
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class FavoritesState(val loading: Boolean = true, val error: String? = null, val items: List<Tile> = emptyList())

@HiltViewModel
class FavoritesViewModel @Inject constructor(private val api: StreamHubApi) : ViewModel() {
    private val _state = MutableStateFlow(FavoritesState())
    val state: StateFlow<FavoritesState> = _state.asStateFlow()

    init { load() }

    fun load() = viewModelScope.launch {
        _state.update { it.copy(loading = true, error = null) }
        runCatching { api.favorites() }
            .onSuccess { l -> _state.update { it.copy(loading = false, items = l) } }
            .onFailure { e -> _state.update { it.copy(loading = false, error = e.message) } }
    }

    fun remove(tile: Tile) = viewModelScope.launch {
        runCatching { api.removeFavorite(tile.kind, tile.id) }
            .onSuccess { _state.update { s -> s.copy(items = s.items.filterNot { it.id == tile.id }) } }
    }
}

/** Favorites across all sources, grouped: Channels · Movies · Shows · VOD/Series · Episodes. Long-press OK removes. */
@Composable
fun FavoritesScreen(vm: FavoritesViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val launcher = LocalPlayLauncher.current
    val t = LocalThemeTokens.current
    when {
        s.loading -> LoadingView()
        s.error != null -> CenterMessage("Couldn't load favorites", s.error) { ShButton("Retry", { vm.load() }, style = ButtonStyle.Primary) }
        s.items.isEmpty() -> CenterMessage("No favorites yet", "Use the Favorite button on a title, or long-press OK on a channel in Live TV.")
        else -> {
            val groups = listOf(
                "Channels" to s.items.filter { it.kind == MediaKind.CHANNEL },
                "Movies" to s.items.filter { it.kind == MediaKind.MOVIE },
                "Shows" to s.items.filter { it.kind == MediaKind.SHOW || it.kind == MediaKind.SEASON },
                "VOD & Series" to s.items.filter { it.kind == MediaKind.VOD || it.kind == MediaKind.SERIES },
                "Episodes" to s.items.filter { it.kind == MediaKind.EPISODE },
            ).filter { it.second.isNotEmpty() }
            PivotScroll(0.dp) {
                LazyColumn(Modifier.fillMaxSize().focusRestorer(), contentPadding = PaddingValues(vertical = Tokens.Canvas.SafeMarginY)) {
                    item("hint") {
                        Text("Long-press OK to remove", style = MaterialTheme.typography.labelMedium, color = t.textMuted, modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX))
                    }
                    groups.forEach { (title, tiles) ->
                        item(title) {
                            FocusRow(tiles, key = { it.id }, title = title) { _, tile, m ->
                                if (tile.kind == MediaKind.CHANNEL) {
                                    ChannelTile(tile.title, null, tile.logo ?: tile.poster, tile.nowPlaying, onClick = { launcher.play(tile.id) }, modifier = m)
                                } else {
                                    PosterTile(tile, onClick = { launcher.openDetails(tile.id) }, onLongClick = { vm.remove(tile) }, modifier = m)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
