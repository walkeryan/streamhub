package com.streamhub.tv.feature.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.HomeResponse
import com.streamhub.tv.core.data.model.MediaRequest
import com.streamhub.tv.core.data.model.TileRequestProgress
import com.streamhub.tv.core.data.repo.progressOrState
import com.streamhub.tv.core.data.model.Notice
import com.streamhub.tv.core.data.repo.RequestsRepository
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import com.streamhub.tv.core.data.model.Row
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.data.repo.catalogScope
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
    private val requests: RequestsRepository,
) : ViewModel() {
    private val refreshRequests = MutableStateFlow(0)
    /** Newer answers about the requests behind Home's posters, by title id; laid over the page so it need not be loaded again. */
    private val requestUpdates = MutableStateFlow<Map<String, MediaRequest>>(emptyMap())
    private val sessionStore = session
    private val localPrefs = prefs
    private val scope = session.session.map { it.catalogScope() }.distinctUntilChanged()
    private val profileId = scope.map { if (it.profileId == null) null else it.toString() }.distinctUntilChanged()
    private val raw = profileHomeLoads(profileId, refreshRequests, ::loadHome)
        .stateIn(viewModelScope, SharingStarted.Eagerly, ProfileHomeLoad<HomeResponse>(null, null))

    val ui: StateFlow<HomeUi> = scope.flatMapLatest { owner ->
        val pid = owner.profileId ?: return@flatMapLatest flowOf<HomeUi>(HomeUi.Loading)
        combine(raw, prefs.rowOrder(pid), prefs.hiddenRows(pid), requestUpdates) { r, order, hidden, updates ->
            val result = r.result
            when {
                r.profileId != owner.toString() || result == null -> HomeUi.Loading
                result.isFailure -> HomeUi.Error(result.exceptionOrNull()?.message ?: "Couldn't load Home")
                else -> {
                    val home = result.getOrThrow()
                    val rows = arrange(home.rows, order, hidden).map { row -> row.copy(tiles = row.tiles.map { withRequestUpdate(it, updates) }) }
                    HomeUi.Ready(home.hero.ifEmpty { home.rows.firstOrNull()?.tiles?.take(5).orEmpty() }.map { withRequestUpdate(it, updates) }, rows)
                }
            }
        }.onStart { emit(HomeUi.Loading) }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, HomeUi.Loading)

    fun refresh() { refreshRequests.update { it + 1 } }

    // ---- Notices ("your request is ready", "your request was declined") --------------------------

    private val _notices = MutableStateFlow<List<Notice>>(emptyList())
    /** Unread notices for the banner row above the hero; empty on servers without them. */
    val notices: StateFlow<List<Notice>> = _notices.asStateFlow()

    init {
        viewModelScope.launch {
            // Per profile, and again on every refresh; one person's notices are never left up for the next.
            combine(profileId, refreshRequests) { profile, _ -> profile }.collectLatest { profile ->
                _notices.value = emptyList()
                requestUpdates.value = emptyMap()
                if (profile != null) _notices.value = try { requests.unreadNotices() } catch (e: CancellationException) { throw e } catch (_: Exception) { emptyList() }
            }
        }
    }

    /**
     * Keeps the request chips on Home's posters current without loading Home again (which would take
     * focus with it): the person's own requests are read and laid over the tiles they are about.
     */
    suspend fun pollRequests() {
        val owner = sessionStore.current().catalogScope()
        val mine = try { requests.mine().requests } catch (e: CancellationException) { throw e } catch (_: Exception) { return }
        if (sessionStore.current().catalogScope() != owner) return
        // Newest first: the first request for a title is the one its poster shows.
        requestUpdates.value = buildMap { mine.forEach { if (it.titleId.isNotBlank()) putIfAbsent(it.titleId, it) } }
    }

    /** Marks [notice] read. It leaves the row at once; a failed call brings it back on the next load. */
    fun dismissNotice(notice: Notice) {
        _notices.update { list -> list.filterNot { it.id == notice.id } }
        viewModelScope.launch {
            try { requests.markRead(notice.id) } catch (e: CancellationException) { throw e } catch (_: Exception) { }
        }
    }

    private suspend fun loadHome(owner: String): HomeResponse {
        val current = sessionStore.current()
        if (current.catalogScope().toString() != owner) throw CancellationException("Profile changed")
        val profile = current.profile?.id ?: throw CancellationException("Profile unavailable")
        // Layout writes remain scoped to the requesting profile. Never publish a
        // response after its profile was replaced, even if a client ignores cancellation.
        val layout = try { api.homeLayout(profile) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { null }
        currentCoroutineContext().ensureActive()
        if (sessionStore.current().catalogScope().toString() != owner) throw CancellationException("Profile changed")
        layout?.takeIf { it.rows.isNotEmpty() }?.let {
            localPrefs.setRowOrder(profile, it.rows.sortedBy { row -> row.sort ?: Int.MAX_VALUE }.map { row -> row.id })
            localPrefs.setHiddenRows(profile, it.rows.filter { row -> row.hidden }.map { row -> row.id }.toSet())
        }
        currentCoroutineContext().ensureActive()
        if (sessionStore.current().catalogScope().toString() != owner) throw CancellationException("Profile changed")
        val home = api.home()
        currentCoroutineContext().ensureActive()
        if (sessionStore.current().catalogScope().toString() != owner) throw CancellationException("Profile changed")
        return home
    }

    /** Poster Adaptive theme: feed the focused poster's dominant colour. */
    fun onAdaptiveColor(c: androidx.compose.ui.graphics.Color?) = themes.setAdaptiveAccent(c)

    companion object {
        /** A tile that shows a request takes the newer answer for it; every other tile is left as the page gave it. */
        internal fun withRequestUpdate(tile: Tile, updates: Map<String, MediaRequest>): Tile {
            if (tile.requestState == null) return tile
            val now = updates[tile.id] ?: return tile
            return tile.copy(requestState = now.state, requestProgress = now.progressOrState().let { TileRequestProgress(it.phase, it.percent) })
        }

        fun arrange(rows: List<Row>, order: List<String>, hidden: Set<String>): List<Row> {
            val visible = rows.filter { it.id !in hidden && it.tiles.isNotEmpty() }
            if (order.isEmpty()) return visible
            val idx = order.withIndex().associate { it.value to it.index }
            return visible.sortedBy { idx[it.id] ?: (order.size + rows.indexOf(it)) }
        }
    }
}

