package com.streamhub.tv.feature.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.HomeResponse
import com.streamhub.tv.core.data.model.Row
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.data.repo.LocalPrefs
import com.streamhub.tv.core.ui.theme.ThemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

sealed interface HomeUi {
    data object Loading : HomeUi
    data class Error(val message: String) : HomeUi
    data class Ready(val hero: List<Tile>, val rows: List<Row>) : HomeUi
}

/**
 * Rows arrive in server order (Continue Watching · On Now · Recently Added · Watchlist · Favorites ·
 * per-source/genre). The profile's local row order / hidden set from Settings is applied on top.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class HomeViewModel @Inject constructor(
    private val api: StreamHubApi,
    session: SessionStore,
    prefs: LocalPrefs,
    private val themes: ThemeRepository,
) : ViewModel() {
    private val raw = MutableStateFlow<Result<HomeResponse>?>(null)

    private val profileId = session.session.map { it.profile?.id ?: "default" }

    val ui: StateFlow<HomeUi> = profileId.flatMapLatest { pid ->
        combine(raw, prefs.rowOrder(pid), prefs.hiddenRows(pid)) { r, order, hidden ->
            when {
                r == null -> HomeUi.Loading
                r.isFailure -> HomeUi.Error(r.exceptionOrNull()?.message ?: "Couldn't load Home")
                else -> {
                    val home = r.getOrThrow()
                    HomeUi.Ready(home.hero.ifEmpty { home.rows.firstOrNull()?.tiles?.take(5).orEmpty() }, arrange(home.rows, order, hidden))
                }
            }
        }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), HomeUi.Loading)

    init { refresh() }

    fun refresh() {
        viewModelScope.launch { raw.value = runCatching { api.home() } }
    }

    /** Poster Adaptive theme: feed the focused poster's dominant colour. */
    fun onAdaptiveColor(c: androidx.compose.ui.graphics.Color?) = themes.setAdaptiveAccent(c)

    companion object {
        fun arrange(rows: List<Row>, order: List<String>, hidden: Set<String>): List<Row> {
            val visible = rows.filter { it.id !in hidden && it.tiles.isNotEmpty() }
            if (order.isEmpty()) return visible
            val idx = order.withIndex().associate { it.value to it.index }
            return visible.sortedBy { idx[it.id] ?: (order.size + rows.indexOf(it)) }
        }
    }
}

