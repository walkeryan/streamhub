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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
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
    private val refreshRequests = MutableStateFlow(0)
    private val sessionStore = session
    private val localPrefs = prefs
    private val profileId = session.session.map { it.profile?.id }.distinctUntilChanged()
    private val raw = profileHomeLoads(profileId, refreshRequests, ::loadHome)
        .stateIn(viewModelScope, SharingStarted.Eagerly, ProfileHomeLoad<HomeResponse>(null, null))

    val ui: StateFlow<HomeUi> = profileId.flatMapLatest { pid ->
        if (pid == null) return@flatMapLatest flowOf<HomeUi>(HomeUi.Loading)
        combine(raw, prefs.rowOrder(pid), prefs.hiddenRows(pid)) { r, order, hidden ->
            val result = r.result
            when {
                r.profileId != pid || result == null -> HomeUi.Loading
                result.isFailure -> HomeUi.Error(result.exceptionOrNull()?.message ?: "Couldn't load Home")
                else -> {
                    val home = result.getOrThrow()
                    HomeUi.Ready(home.hero.ifEmpty { home.rows.firstOrNull()?.tiles?.take(5).orEmpty() }, arrange(home.rows, order, hidden))
                }
            }
        }.onStart { emit(HomeUi.Loading) }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, HomeUi.Loading)

    fun refresh() { refreshRequests.update { it + 1 } }

    private suspend fun loadHome(profile: String): HomeResponse {
        // Layout writes remain scoped to the requesting profile. Never publish a
        // response after its profile was replaced, even if a client ignores cancellation.
        val layout = try { api.homeLayout(profile) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { null }
        currentCoroutineContext().ensureActive()
        if (sessionStore.current().profile?.id != profile) throw CancellationException("Profile changed")
        layout?.takeIf { it.rows.isNotEmpty() }?.let {
            localPrefs.setRowOrder(profile, it.rows.sortedBy { row -> row.sort ?: Int.MAX_VALUE }.map { row -> row.id })
            localPrefs.setHiddenRows(profile, it.rows.filter { row -> row.hidden }.map { row -> row.id }.toSet())
        }
        currentCoroutineContext().ensureActive()
        if (sessionStore.current().profile?.id != profile) throw CancellationException("Profile changed")
        val home = api.home()
        currentCoroutineContext().ensureActive()
        if (sessionStore.current().profile?.id != profile) throw CancellationException("Profile changed")
        return home
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

