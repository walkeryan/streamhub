package com.streamhub.tv.feature.details

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.sameSwitchSession
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.Permissions
import com.streamhub.tv.core.data.model.Room
import com.streamhub.tv.core.data.repo.MeRepository
import com.streamhub.tv.core.data.repo.RoomRepository
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
    /** The signed-in user may start a watch room (rooms.create). */
    val canWatchTogether: Boolean = false,
    /** "Watch together" in progress: the room being opened, or open and waiting for Start. */
    val together: WatchTogether? = null,
)

/** The "Watch together" dialog: a room is created first, shown with its code, then joined with Start. */
sealed interface WatchTogether {
    data object Creating : WatchTogether
    data class Failed(val error: UserError) : WatchTogether
    data class Ready(
        val room: Room,
        /** `<server>/room/<code>` for the QR code. */
        val shareUrl: String?,
        val starting: Boolean = false,
        val error: UserError? = null,
        /** The room's source has no confirmed audio in this profile's languages; Start asks to go ahead with it. */
        val needsAudioConsent: Boolean = false,
    ) : WatchTogether
}

@HiltViewModel
class DetailsViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val sessions: SessionStore,
    private val rooms: RoomRepository,
    private val me: MeRepository,
    savedState: SavedStateHandle,
) : ViewModel() {
    val titleId: String = checkNotNull(savedState["id"])
    private val _state = MutableStateFlow(DetailsState())
    val state: StateFlow<DetailsState> = _state.asStateFlow()

    private var detailOwner:Session?=null

    init {
        load()
        viewModelScope.launch { me.state.collect { access -> _state.update { it.copy(canWatchTogether = access.can(Permissions.ROOMS_CREATE)) } } }
    }

    // ---- Watch together ------------------------------------------------------------------------

    /** Creates the room; the dialog then shows its code until Start (which joins it) or Cancel (which closes it). */
    fun watchTogether() {
        if (_state.value.together is WatchTogether.Creating || _state.value.together is WatchTogether.Ready) return
        _state.update { it.copy(together = WatchTogether.Creating) }
        viewModelScope.launch {
            val next = try {
                val room = rooms.create(titleId)
                WatchTogether.Ready(room, rooms.shareUrl(room))
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { WatchTogether.Failed(ApiErrors.describe(e)) }
            _state.update { if (it.together == WatchTogether.Creating) it.copy(together = next) else it }
        }
    }

    fun startRoom() {
        val ready = _state.value.together as? WatchTogether.Ready ?: return
        if (ready.starting) return
        _state.update { it.copy(together = ready.copy(starting = true, error = null)) }
        viewModelScope.launch {
            try {
                val key = rooms.join(ready.room.code, allowUnverifiedAudio = ready.needsAudioConsent)
                _state.update { it.copy(together = null) }
                rooms.openPlayer(key)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                val consent = (e as? ApiException)?.error?.code == "audio_selection_required" && !ready.needsAudioConsent
                _state.update {
                    it.copy(together = ready.copy(starting = false, needsAudioConsent = consent || ready.needsAudioConsent, error = if (consent) null else ApiErrors.describe(e)))
                }
            }
        }
    }

    /** Closes the dialog; a room nobody was let into is closed again rather than left to expire. */
    fun cancelRoom() {
        val together = _state.value.together
        if (together is WatchTogether.Ready && together.starting) return
        _state.update { it.copy(together = null) }
        if (together is WatchTogether.Ready) viewModelScope.launch {
            try { rooms.close(together.room.code) } catch (e: CancellationException) { throw e } catch (_: Exception) { }
        }
    }

    fun load() {
        viewModelScope.launch {
            val owner=sessions.current()
            _state.update { it.copy(loading = true, error = null) }
            runCatching { api.title(titleId) }
                .onSuccess { d ->
                    if(!sameSwitchSession(owner,sessions.current())) return@onSuccess
                    detailOwner=owner
                    _state.update { it.copy(loading = false, detail = d, seasonEpisodes = d.episodes, selectedSeason = d.seasons.firstOrNull()?.id) }
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
