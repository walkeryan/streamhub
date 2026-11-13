package com.streamhub.tv.feature.details

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.sameSwitchSession
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.Permissions
import com.streamhub.tv.core.data.model.RequestProgress
import com.streamhub.tv.core.data.model.RequestStates
import com.streamhub.tv.core.data.repo.phaseOfState
import com.streamhub.tv.core.data.repo.progressOrState
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.data.repo.MeRepository
import com.streamhub.tv.core.data.repo.RequestsRepository
import com.streamhub.tv.core.data.repo.RoomDirectory
import com.streamhub.tv.core.data.repo.RoomSubject
import com.streamhub.tv.core.data.repo.WatchTogetherLauncher
import com.streamhub.tv.core.data.repo.canPickSeasons
import com.streamhub.tv.core.data.repo.requestTarget
import kotlinx.coroutines.CancellationException
import com.streamhub.tv.core.data.model.Favorite
import com.streamhub.tv.core.data.model.PlaybackState
import com.streamhub.tv.core.data.model.ProgressReport
import com.streamhub.tv.core.data.model.TitleDetail
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class DetailsState(
    val loading: Boolean = true,
    val error: String? = null,
    val detail: TitleDetail? = null,
    val inWatchlist: Boolean = false,
    val selectedSeason: String? = null,
    val seasonEpisodes: List<com.streamhub.tv.core.data.model.Tile> = emptyList(),
    val toast: String? = null,
    /** The signed-in user may ask for titles (requests.create). */
    val canRequest: Boolean = false,
    /** Where this title's request stands; starts from the detail and follows a request made here. */
    val requestState: String? = null,
    /** What that request is doing: the title's own answer (phase and percentage), or the request's in full once it is known. */
    val requestProgress: RequestProgress? = null,
    /** The request itself, when it is this person's (or theirs to approve): it can then be followed and checked by id. */
    val requestId: String? = null,
    val requesting: Boolean = false,
    /** A series whose seasons can be picked: the season dialog is open. */
    val seasonPicker: Boolean = false,
    val requestError: UserError? = null,
    /** The signed-in user may start a watch room (rooms.create). */
    val canWatchTogether: Boolean = false,
)

@HiltViewModel
class DetailsViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val sessions: SessionStore,
    rooms: RoomDirectory,
    private val me: MeRepository,
    private val requests: RequestsRepository,
    savedState: SavedStateHandle,
) : ViewModel() {
    val titleId: String = checkNotNull(savedState["id"])
    private val _state = MutableStateFlow(DetailsState())
    val state: StateFlow<DetailsState> = _state.asStateFlow()

    private var detailOwner:Session?=null

    init {
        load()
        viewModelScope.launch {
            me.state.collect { access ->
                _state.update { it.copy(canWatchTogether = access.can(Permissions.ROOMS_CREATE), canRequest = access.can(Permissions.REQUESTS_CREATE)) }
            }
        }
    }

    // ---- Request -------------------------------------------------------------------------------

    /** "Request": a film or a whole series at once, or the season dialog when seasons can be picked. */
    fun request() {
        val d = _state.value.detail ?: return
        val target = requestTarget(d.id) ?: return
        if (_state.value.requesting) return
        if (canPickSeasons(target, d.requestSeasons)) _state.update { it.copy(seasonPicker = true) } else submitRequest(null)
    }

    /** [seasons] null = the whole series. */
    fun submitRequest(seasons: List<Int>?) {
        val d = _state.value.detail ?: return
        val target = requestTarget(d.id) ?: return
        if (_state.value.requesting) return
        _state.update { it.copy(requesting = true, seasonPicker = false, requestError = null) }
        viewModelScope.launch {
            try {
                val made = requests.request(target, seasons)
                _state.update { it.copy(requesting = false, requestState = made.state, requestProgress = made.progressOrState(), requestId = made.id) }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(requesting = false, requestError = ApiErrors.describe(e)) } }
        }
    }

    /**
     * Follows the request while the page is in front. With the request's id it is read by id (the
     * server refreshes a stale answer first); a request that is someone else's is followed through
     * the title's own answer. Never blanks the page: only what changed is replaced. When the title
     * becomes playable the detail is replaced too, so Play appears without leaving the page.
     */
    suspend fun pollRequest() {
        val before = _state.value
        if (before.requestState == null || before.requesting) return
        try {
            val id = before.requestId
            if (id != null) {
                val now = requests.one(id)
                _state.update { it.copy(requestState = now.state, requestProgress = now.progressOrState()) }
                if (now.state != RequestStates.AVAILABLE || before.detail?.sources?.any { it.playableInApp } == true) return
            }
            val owner = sessions.current()
            val d = api.title(titleId)
            if (!sameSwitchSession(owner, sessions.current())) return
            _state.update {
                val playable = d.sources.any { b -> b.playableInApp }
                it.copy(
                    detail = if (playable != (it.detail?.sources?.any { b -> b.playableInApp } == true)) d else it.detail,
                    requestState = if (id == null) d.requestState else it.requestState,
                    requestProgress = if (id == null) d.requestProgress?.let { p -> RequestProgress(p.phase, percent = p.percent) } else it.requestProgress,
                )
            }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { }
    }

    /** Select on the status button: ask the request manager now ("Check again"), and say that it was done. */
    fun checkRequest() {
        val id = _state.value.requestId
        viewModelScope.launch {
            try {
                if (id != null) {
                    val now = requests.refresh(id)
                    _state.update { it.copy(requestState = now.state, requestProgress = now.progressOrState(), toast = "Checked just now.") }
                } else { pollRequest(); _state.update { it.copy(toast = "Checked just now.") } }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(toast = ApiErrors.describe(e).title) } }
        }
    }

    /** Finds this person's own request for the title, so its full progress (detail, time left) can be shown and followed. */
    private suspend fun findRequest(d: TitleDetail) {
        if (d.requestState == null || !me.can(Permissions.REQUESTS_CREATE)) return
        try {
            val mine = requests.mine().requests.firstOrNull { it.titleId == d.id && it.state == d.requestState } ?: return
            _state.update { if (it.detail?.id == d.id) it.copy(requestId = mine.id, requestProgress = mine.progressOrState()) else it }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { }
    }

    fun closeSeasonPicker() = _state.update { it.copy(seasonPicker = false) }
    fun dismissRequestError() = _state.update { it.copy(requestError = null) }
    fun say(text: String) = _state.update { it.copy(toast = text) }

    // ---- Watch together ------------------------------------------------------------------------

    /** The room dialog, shared by this title's own button and by an episode's actions. */
    val together = WatchTogetherLauncher(rooms, viewModelScope)

    /** This title: a film or an episode as it is, a series on the next episode the server resolves. */
    fun watchTogether() {
        val d = _state.value.detail ?: return
        together.open(d.id, d.title, if (d.kind in SERIES_KINDS) RoomSubject.Series else RoomSubject.Title)
    }

    /** One episode of the series on screen. */
    fun watchEpisodeTogether(episode: Tile) {
        val show = _state.value.detail?.title.orEmpty()
        together.open(episode.id, listOf(show, episode.title).filter(String::isNotBlank).joinToString(" · "), RoomSubject.Title)
    }

    fun load() {
        viewModelScope.launch {
            val owner=sessions.current()
            _state.update { it.copy(loading = true, error = null) }
            runCatching { api.title(titleId) }
                .onSuccess { d ->
                    if(!sameSwitchSession(owner,sessions.current())) return@onSuccess
                    detailOwner=owner
                    _state.update {
                        it.copy(loading = false, detail = d, seasonEpisodes = d.episodes, selectedSeason = d.seasons.firstOrNull()?.id, requestState = d.requestState,
                            requestProgress = d.requestProgress?.let { p -> RequestProgress(p.phase, percent = p.percent) } ?: phaseOfState(d.requestState)?.let { phase -> RequestProgress(phase) },
                            requestId = null)
                    }
                    findRequest(d)
                    runCatching { api.watchlist() }.onSuccess { wl -> _state.update { it.copy(inWatchlist = wl.any { t -> t.id == titleId }) } }
                }
                .onFailure { e -> _state.update { it.copy(loading = false, error = e.message) } }
        }
    }

    fun selectSeason(id: String) {
        _state.update { it.copy(selectedSeason = id) }
        viewModelScope.launch {
            runCatching { api.title(id) }.onSuccess { s -> _state.update { it.copy(seasonEpisodes = s.episodes) } }
        }
    }

    fun toggleFavorite() {
        val d = _state.value.detail ?: return
        viewModelScope.launch {
            runCatching { if (d.isFavorite) api.removeFavorite(d.kind, d.id) else api.addFavorite(Favorite(d.kind, d.id)) }
                .onSuccess { _state.update { it.copy(detail = d.copy(isFavorite = !d.isFavorite), toast = if (d.isFavorite) "Removed from favorites" else "Added to favorites") } }
        }
    }

    fun toggleWatchlist() {
        val inList = _state.value.inWatchlist
        viewModelScope.launch {
            runCatching { if (inList) api.removeFromWatchlist(titleId) else api.addToWatchlist(titleId) }
                .onSuccess { _state.update { it.copy(inWatchlist = !inList, toast = if (inList) "Removed from watchlist" else "Added to watchlist") } }
        }
    }

    /** Mark watched: contract — state=stopped with position_ms >= duration_ms marks the item watched. */
    fun markWatched() {
        val d = _state.value.detail ?: return
        val dur = (d.runtimeMin ?: 1) * 60_000L
        val owner=detailOwner ?: return
        viewModelScope.launch {
            runCatching { api.reportProgress(ProgressReport(id=d.id,expectedProfileId=owner.profile?.id,positionMs=dur,durationMs=dur,state=PlaybackState.STOPPED),owner) }
                .onSuccess { saved -> if(saved && sameSwitchSession(owner,sessions.current())) _state.update { it.copy(detail = d.copy(progressPct = 100.0), toast = "Marked as watched") } }
        }
    }

    fun clearToast() = _state.update { it.copy(toast = null) }
}

/** Titles that are a set of episodes: a room on one opens on the next episode to watch. */
internal val SERIES_KINDS = setOf(MediaKind.SHOW, MediaKind.SERIES, MediaKind.SEASON)
