package com.streamhub.tv.feature.player

import android.os.SystemClock
import androidx.lifecycle.SavedStateHandle
import androidx.media3.common.Player
import com.streamhub.tv.core.data.repo.MeRepository
import com.streamhub.tv.core.data.repo.ProfileFilterStore
import com.streamhub.tv.core.data.repo.RoomRepository
import com.streamhub.tv.core.data.model.UpNext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.JsonPrimitive
import com.streamhub.tv.core.data.repo.RoomSeat
import com.streamhub.tv.core.data.repo.WatchTogether
import com.streamhub.tv.core.data.repo.WatchTogetherLauncher
import com.streamhub.tv.core.data.model.Permissions
import com.streamhub.tv.core.data.model.Room
import com.streamhub.tv.core.data.room.roomShareUrl
import com.streamhub.tv.core.data.room.RoomClient
import com.streamhub.tv.core.data.room.RoomClock
import com.streamhub.tv.core.data.room.RoomConnection
import com.streamhub.tv.core.data.room.LiveStep
import com.streamhub.tv.core.data.room.RoomStream
import com.streamhub.tv.core.data.room.RoomSync
import com.streamhub.tv.core.data.room.RoomUiState
import com.streamhub.tv.core.data.room.StreamStep
import com.streamhub.tv.core.data.room.SyncAction
import com.streamhub.tv.core.data.room.roomClosedMessage
import com.streamhub.tv.core.data.room.roomRefusalText
import com.streamhub.tv.core.data.room.roomSeatLost
import com.streamhub.tv.core.data.room.roomSeatRecoverable
import com.streamhub.tv.core.data.room.roomStream
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamRefusals
import com.streamhub.tv.core.data.repo.SessionEnd
import kotlinx.coroutines.flow.combine
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.handoff.DeepLinkLauncher
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.repo.catalogScope
import com.streamhub.tv.core.data.repo.resolveArchiveSelection
import com.streamhub.tv.core.data.repo.ExpiredArchiveSelection
import com.streamhub.tv.core.data.model.RewindCapability
import com.streamhub.tv.core.data.model.BufferWindow
import com.streamhub.tv.core.data.model.BufferLease
import com.streamhub.tv.core.data.model.BufferLeaseAction
import com.streamhub.tv.core.data.model.BufferSeekResult
import com.streamhub.tv.core.data.repo.BufferLeaseApi
import com.streamhub.tv.core.data.repo.BufferLeaseSession
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import com.streamhub.tv.core.data.model.PlaybackRequest
import com.streamhub.tv.core.data.repo.AudioPlaybackAttempt
import com.streamhub.tv.core.data.repo.audioPlaybackAttempt
import com.streamhub.tv.core.data.repo.CatalogScope
import com.streamhub.tv.core.data.model.Channel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.first
import java.time.Instant
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.repo.ChannelListCache
import com.streamhub.tv.core.data.repo.PlaybackCoordinator
import com.streamhub.tv.core.player.CaptionAppearance
import com.streamhub.tv.core.player.AspectMode
import com.streamhub.tv.core.player.PlayerFactory
import com.streamhub.tv.core.player.StreamHubPlayer
import com.streamhub.tv.core.ui.input.ChannelNumberEntry
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/** [Controls] is the focusable film control row; [Osd] is the passive live banner. */
enum class Overlay { None, Osd, Controls, MiniList, Menu, Rewind, Buffer }

/** The audio or subtitle list opened from the control row. */
enum class TrackPicker { Audio, Subtitles }

data class PlayerScreenState(
    val descriptor: PlaybackDescriptor? = null,
    val rewind: RewindCapability? = null,
    val rewindLoading: Boolean = false,
    val rewindError: String? = null,
    val audioChoice: AudioPlaybackAttempt.Choice? = null,
    val bufferWindow: BufferWindow? = null,
    val bufferBusy: Boolean = false,
    val bufferError: String? = null,
    val bufferTimeshift: Boolean = false,

    val live: Boolean = false,
    val overlay: Overlay = Overlay.Osd,
    /** Channel shown in the OSD: the playing one, or the zap target while the user is still pressing Up/Down. */
    val osdChannel: Channel? = null,
    val channels: List<Channel> = emptyList(),
    val index: Int = -1,
    val numberEntry: String = "",
    val message: String? = null,
    val aspect: AspectMode = AspectMode.FIT,
    val sleepMinutes: Int? = null,
    val sleepEndsAt: Long? = null,
    /** A failure that needs an answer (Try again / Back); shown as a dialog. */
    val failure: UserError? = null,
    /** The failure came from a channel change that left the previous stream running: dismissing keeps watching. */
    val failureKeepsPlaying: Boolean = false,
    val retrying: Boolean = false,
    /** Where the scrubber is heading while Left/Right is still being pressed; null once the seek has landed. */
    val scrubTargetMs: Long? = null,
    /** Set while playing as a member of a watch room: its code, and what the room last said. */
    val roomCode: String? = null,
    val room: RoomUiState? = null,
    /** Back was pressed in a room: "Leave the room?" is up. */
    val leaveAsk: Boolean = false,
    /** The room is over (closed by the host, expired, seat lost); acknowledged with Back to the title. */
    val roomEnded: UserError? = null,
    /** [retrying] because the room moved on (a seek restarted its shared stream) or this member lost its place, not because Try again was pressed. */
    val roomCatchUp: Boolean = false,
    /**
     * Playing a room's one shared stream, which starts part-way into the title: where its media time
     * zero is, and the title's full length. The player's own position and duration are the stream's.
     */
    val streamStartMs: Long = 0,
    val titleDurationMs: Long? = null,
    /** The profile may start watch rooms (`rooms.create`), as read when this player opened. */
    val mayStartRoom: Boolean = false,
    /** `<server>/room/<code>` of the room this player is in, for the QR code of "Invite people". */
    val roomShareUrl: String? = null,
    // ---- Revamped player (UpNextFlow) ----
    /** What this title allows: skip intro/credits, Up next, the episode buttons. */
    val upNext: UpNextFlow.Plan = UpNextFlow.NONE,
    /** The profile's `autoplay_next` (missing = on), read when the player opened. */
    val autoplayNext: Boolean = true,
    val captions: CaptionAppearance? = null,
    /** The profile's `back_minimises` (missing = off): Back shrinks a playing title into the mini player. */
    val backMinimises: Boolean = false,
    /** Skip intro is up and seeks here. */
    val skipIntroTo: Long? = null,
    val skipCredits: Boolean = false,
    /** The Up next card, while it is up. */
    val upNextCard: UpNextCardModel? = null,
    /** Still watching? is up (the picture is paused behind it). */
    val stillWatching: Boolean = false,
    /** The next (or previous) episode is being resolved. */
    val advancing: Boolean = false,
    /** Where focus lands when the control row comes up. */
    val controlsFocus: ControlsFocus = ControlsFocus.PlayPause,
    val picker: TrackPicker? = null,
) {
    /** Position in the title for a position of the player. */
    fun titlePositionMs(playerPositionMs: Long): Long = playerPositionMs + streamStartMs
    /** The title's length: the room's word for a shared stream, the player's otherwise. */
    fun titleDurationMs(playerDurationMs: Long): Long = titleDurationMs ?: playerDurationMs
    /** A shared stream is seeked by the room (the server starts it again), whatever the player can do on its own. */
    fun canSeek(playerSeekable: Boolean): Boolean = playerSeekable || (titleDurationMs ?: 0) > 0
}

/**
 * Full-screen player logic.
 *  - Descriptor arrives pre-resolved from PlaybackCoordinator (no second /playback/resolve, no interstitial).
 *  - Live zapping: Up/Down/CH± walk the channel_context list; the OSD updates instantly and the
 *    stream switches after 350 ms of no further presses so fast zapping doesn't spam the server.
 *  - Number entry commits 2 s after the last digit (or immediately on OK).
 *  - Last channel toggle (Back-twice).
 *  - Sleep timer → pause and exit.
 *  - Films and buffered history get a focusable control row (scrubber + buttons) instead of the live banner.
 *  - "Try again" resolves the accepted request again; it never replays the descriptor on screen.
 *  - A stream that answers 403 `playback_paused` is resolved again once with a fresh generation before
 *    anything is said: after a server restart that is all it takes. Only a second such answer, or a
 *    server that refuses the fresh resolve, shows "Playback is stopped".
 *  - A refusal of the media URL itself (StreamRefusals: the session or account, the home network, the
 *    rating ceiling, the user's stream limit) is worded like the same refusal of a resolve. One about
 *    the session is first put to the server: refreshed and resolved again once, or, when the session
 *    really is over, left to the shell, which explains and returns to the picker or to pairing.
 *  - Feed failover: a live channel that is one of several feeds of a game in progress and whose
 *    picture stops for more than five seconds moves on to the next feed of the game (FeedFailover),
 *    naming the channel it left so the server frees that stream at once. Never in a room.
 *  - In a watch room (the player key carries a RoomSeat) play, pause and seek go to the room instead
 *    of the player; what the room answers is applied through RoomSync, so every member, this one
 *    included, moves on the same message. Buffering is reported so the room can wait.
 *  - A room that plays one shared stream names its `stream_epoch` in every `state`. When that is not
 *    the epoch being played, the member's descriptor is fetched again (GET /rooms/{code}/descriptor),
 *    the media item reloaded, and RoomSync positions it against the stream's new time zero. The same
 *    call puts a member back whose player failed; a lost seat is taken up again with the member token.
 */
@HiltViewModel
class PlayerViewModel @Inject constructor(
    savedState: SavedStateHandle,
    private val coordinator: PlaybackCoordinator,
    private val channelCache: ChannelListCache,
    private val launcher: DeepLinkLauncher,
    private val api: StreamHubApi,
    private val sessions: SessionStore,
    private val rooms: RoomRepository,
    private val me: MeRepository,
    private val prefs: ProfileFilterStore,
    playerFactory: PlayerFactory,
) : ViewModel() {
    private val key: String = checkNotNull(savedState["key"])
    /** The paired server, for a scrub thumbnail address given relative to it. */
    var serverUrl: String? = null
        private set
    val player: StreamHubPlayer = playerFactory.create()

    private val _state = MutableStateFlow(PlayerScreenState())
    val state: StateFlow<PlayerScreenState> = _state.asStateFlow()

    private val _exit = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val exit: SharedFlow<Unit> = _exit

    private var lastChannelId: String? = null
    private var currentStopped = false
    private var zapJob: Job? = null
    private var numberJob: Job? = null
    private var osdJob: Job? = null
    private var sleepJob: Job? = null
    private var rewindJob: Job? = null
    private var attachJob: Job? = null
    private var tunedAtElapsedMs: Long? = null
    private var lastBufferSeek: Instant? = null
    private var bufferHeartbeat: Job? = null
    private var bufferSeekJob: Job? = null
    private var scrubJob: Job? = null
    /** The accepted request (source and audio choice included) behind the descriptor on screen. */
    private var currentRequest: PlaybackRequest? = null
    /** A channel change that failed; "Try again" repeats it rather than the stream that was playing. */
    private var failedRequest: PlaybackRequest? = null
    private val cleanupScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val buffer = BufferLeaseSession(object: BufferLeaseApi {
        override suspend fun acquire(token: String): BufferLease = api.acquireBuffer(token)
        override suspend fun heartbeat(action: BufferLeaseAction): BufferWindow = api.heartbeatBuffer(action)
        override suspend fun seek(action: BufferLeaseAction): BufferSeekResult = api.seekBuffer(action)
        override suspend fun release(action: BufferLeaseAction) = api.releaseBuffer(action)
    })
    private var audioOwner: CatalogScope? = null
    private var choiceArchiveAction = false
    /** This player's seat in a watch room; null for ordinary playback. */
    private var seat: RoomSeat? = rooms.seat(key)
    private var roomClient: RoomClient? = null
    private var roomJob: Job? = null
    private var messageJob: Job? = null
    /** The shared stream being played (the descriptor's epoch and time zero); null for a member's own stream. */
    private var playingStream: RoomStream? = seat?.descriptor?.roomStream()
    /** Recoveries of the room's stream made without being asked, since the picture last moved. */
    private var autoRecoveries = 0
    private var lastAutoRejoinAt = 0L
    /** Counts recoveries, so one that was superseded does not clear the state of the one that replaced it. */
    private var recoverySeq = 0
    /** Recoveries in a row that ended with the room still on a later start of its stream than the one loaded. */
    private var epochMisses = 0
    /** Set between moving this playback into a room it started and putting that room where the TV was. */
    @Volatile private var carryOver: CarryOver? = null

    /** "Watch together" from the menu: the room is made and shown, and Start moves this playback into it. */
    private val together = WatchTogetherLauncher(rooms, viewModelScope, join = ::switchIntoRoom)
    val watchTogether: StateFlow<WatchTogether?> = together.state
    /** Where this episode's Up next card is (see UpNextFlow); reset with every title. */
    private var flow = UpNextFlow.State()
    /** Episodes started by autoplay since the last key press (or mini-player command): Still watching? at three. */
    private var unattendedAutoplays = 0

    init {
        coordinator.registerActivePlayer(key) { stopCurrentPlayback() }
        val d = coordinator.take(key)
        if (d == null) {
            // Reached when the system restores this screen after the process died: the resolved stream is gone.
            _state.update { it.copy(failure = UserError("Nothing to play", "This screen was reopened without its stream. Go back and press Play again.", canRetry = false)) }
        } else {
            currentRequest = coordinator.requestFor(key) ?: PlaybackRequest(d.id)
            // Known before the first frame so the right overlay (live banner or film controls) is chosen.
            _state.update { it.copy(live = d.live) }
            seat?.let(::startRoom)
            attachJob = viewModelScope.launch { start(d); if(d.live) loadChannelList(d) }
        }
        showOsd()
        viewModelScope.launch { player.state.map { it.refusal }.distinctUntilChanged().collect { refusal -> if (refusal != null) streamRefused(refusal) } }
        if (seat == null) viewModelScope.launch {
            player.state.map { it.incompatibleVariant }.distinctUntilChanged().collect { variant ->
                val descriptor = _state.value.descriptor
                val request = currentRequest
                if (variant != null && descriptor?.variantId == variant && descriptor.tvCompatibleAvailable &&
                    request != null && !request.preferHls && !currentStopped && seat == null) {
                    // Retry the file the server actually selected (including the exact episode).
                    currentRequest = request.copy(id = descriptor.id, variantId = variant, preferHls = true,
                        audioTrack = request.audioTrack ?: descriptor.audioTrack, replaces = descriptor.id)
                    retry()
                }
            }
        }
        // A room plays one stream for everyone: it never moves to another feed on its own.
        if (seat == null) viewModelScope.launch { watchFeeds() }
        viewModelScope.launch { player.state.map { it.isPlaying }.distinctUntilChanged().collect { if (it) { reauthorized = false; pausedRetry.reset() } } }
        viewModelScope.launch { serverUrl = sessions.current().serverUrl; loadPlaybackPrefs() }
        viewModelScope.launch { while (true) { delay(FLOW_TICK_MS); flowTick() } }
        viewModelScope.launch {
            // Read afresh for the person playing: what was cached may be from before a switch in the web app.
            try { me.refresh() } catch (e: CancellationException) { throw e } catch (_: Exception) { }
            me.state.collect { access -> _state.update { it.copy(mayStartRoom = access.can(Permissions.ROOMS_CREATE)) } }
        }
        viewModelScope.launch {
            sessions.session.map { it.catalogScope() }.distinctUntilChanged().drop(1).collect {
                rewindJob?.cancel(); _state.update { it.copy(audioChoice=null) }; stopCurrentPlayback(); _exit.emit(Unit)
            }
        }
    }

    // ---- The server refused the stream itself ------------------------------------------------------

    /** One silent refresh-and-resolve per stretch of playback: a stream refused again straight away is put to the viewer. */
    private var reauthorized = false

    private fun streamRefused(refusal: ApiException) {
        if (refusal.error?.code in StreamRefusals.ABOUT_THE_SESSION && !reauthorized) { reauthorize(); return }
        if (refusal.error?.code == StreamRefusals.PLAYBACK_PAUSED) { resumeAfterPause(refusal); return }
        // Segments of a start of the room's stream that a seek has replaced, and this member missed the
        // `state` that said so: the room's descriptor is fetched again, a few times before the viewer is asked.
        if (refusal.error?.code == StreamRefusals.STREAM_EPOCH_STALE && seat != null && _state.value.roomEnded == null && autoRecoveries < MAX_AUTO_RECOVERIES) {
            autoRecoveries++
            recoverRoom(auto = true)
            return
        }
        // Worded as the same refusal of a resolve would be; "Try again" resolves afresh.
        attachJob?.cancel()
        _state.update { it.copy(failure = ApiErrors.describe(refusal), failureKeepsPlaying = false, retrying = false, roomCatchUp = false) }
    }

    /**
     * The stream says its session no longer stands. Any authenticated call settles what that means:
     * a stale session (a role, permission or limit changed) is refreshed by the client on the way and
     * playback is resolved again, once; a session that is revoked, or an account that is suspended,
     * fails the call the same way, and the shell is told by the client itself (see SessionEvents).
     */
    private fun reauthorize() {
        reauthorized = true
        attachJob?.cancel(); rewindJob?.cancel()
        _state.update { it.copy(failure = null, retrying = true, roomCatchUp = false, message = null) }
        attachJob = viewModelScope.launch {
            try { api.me() }
            catch (e: CancellationException) { _state.update { it.copy(retrying = false) }; throw e }
            catch (e: Exception) {
                _state.update { it.copy(retrying = false) }
                if (SessionEnd.ofCode((e as? ApiException)?.error?.code) != null) {
                    // Over for good or for now: nothing here can play. The shell explains and moves on.
                    stopCurrentPlayback()
                    _exit.emit(Unit)
                } else _state.update { it.copy(failure = ApiErrors.describe(e), failureKeepsPlaying = false) }
                return@launch
            }
            _state.update { it.copy(retrying = false) }
            retry()
        }
    }

    /** One silent recovery per playback from 403 `playback_paused` on the stream (see [PausedRetry]). */
    private val pausedRetry = PausedRetry()

    /**
     * The stream answered `playback_paused`. After a server restart every stream issued before it
     * answers so, though nothing was stopped: the first such answer for a playback is resolved again
     * with a fresh generation, without a dialog. If the server really is stopped it refuses the
     * generation (or the resolve) the same way, and that, like a second refusal of the stream, is shown.
     */
    private fun resumeAfterPause(refusal: ApiException) {
        val held = seat
        val stale = _state.value.descriptor
        val request = currentRequest ?: stale?.let { PlaybackRequest(it.id) }
        attachJob?.cancel(); rewindJob?.cancel()
        if (!pausedRetry.claim() || (held == null && request == null)) {
            _state.update { it.copy(failure = ApiErrors.describe(refusal), failureKeepsPlaying = false, retrying = false, roomCatchUp = false) }
            return
        }
        // A film resumes where it stopped; live and archive streams restart where the server puts them.
        val resumeAt = if (stale != null && !stale.live) playbackPosition(stale) else null
        val mine = ++recoverySeq
        _state.update { it.copy(failure = null, retrying = true, roomCatchUp = false, message = null) }
        attachJob = viewModelScope.launch {
            val owner = sessions.current().catalogScope()
            val outcome = try {
                resumeWithFreshGeneration({ api.playbackGeneration().generation }) { generation ->
                    if (held != null) reloadRoom(held, rejoin = false)
                    else {
                        // Give back this player's own connection first: a one-stream source would answer source_busy.
                        releaseCurrentBuffer()
                        val accepted = checkNotNull(request)
                        val fresh = coordinator.resolveAgain(accepted.copy(
                            recoveryGeneration = generation,
                            startPositionMs = if (accepted.preferHls) resumeAt else accepted.startPositionMs,
                        ))
                        currentCoroutineContext().ensureActive()
                        if (sessions.current().catalogScope() != owner) throw CancellationException("Playback scope changed")
                        start(fresh, resumeAt)
                        if (fresh.live) loadChannelList(fresh)
                    }
                }
            } catch (e: CancellationException) {
                if (recoverySeq == mine) _state.update { it.copy(retrying = false) }
                throw e
            }
            if (recoverySeq != mine) return@launch
            _state.update { it.copy(retrying = false) }
            outcome.onSuccess { showOsd() }.onFailure { e -> _state.update { it.copy(failure = ApiErrors.describe(e), failureKeepsPlaying = false) } }
        }
    }

    private suspend fun releaseCurrentBuffer() {
        lastBufferSeek = null
        tunedAtElapsedMs = null
        _state.update { it.copy(bufferWindow = null, bufferTimeshift = false, bufferError = null,
            overlay = if (it.overlay == Overlay.Buffer) Overlay.None else it.overlay) }
        bufferHeartbeat?.cancel(); bufferHeartbeat=null
        bufferSeekJob?.cancel(); bufferSeekJob=null
        player.stop(); currentStopped=true
        withContext(NonCancellable) { buffer.close() }
        _state.update { it.copy(bufferWindow=null, bufferBusy=false,bufferTimeshift=false) }
    }

    private suspend fun stopCurrentPlayback() {
        attachJob?.cancel(); attachJob=null
        releaseCurrentBuffer()
    }

    private suspend fun start(descriptor: PlaybackDescriptor, startPositionMs: Long? = null) {
        // A room runs on the room clock: no private timeshift buffer, and it starts where the room is.
        val inRoom = seat != null
        val d = if (inRoom) descriptor.copy(bufferAvailable = false) else descriptor
        releaseCurrentBuffer()
        if (d.mode == PlaybackMode.HANDOFF) {
            d.handoff?.let { launcher.launch(it) }; _exit.tryEmit(Unit); return
        }
        val reportOwner=sessions.current()
        val owner=reportOwner.catalogScope()
        flow = UpNextFlow.State()
        val plan = UpNextFlow.plan(d, currentRequest, inRoom)
        _state.update { it.copy(descriptor=d, live=d.live, message=null, bufferError=null, bufferBusy=d.bufferAvailable, upNext=plan, skipIntroTo=null, skipCredits=false, upNextCard=null, stillWatching=false) }
        var acquiredLeaseId: String? = null
        try {
            val lease = if (d.bufferAvailable) try {
                buffer.open(d)
            } catch (error: Exception) {
                if (currentRequest?.bufferAt != null || !optionalBufferUnavailable(error)) throw error
                null
            } else null
            acquiredLeaseId=lease?.leaseId
            currentCoroutineContext().ensureActive()
            if(sessions.current().catalogScope()!=owner) throw CancellationException("Playback scope changed")
            currentStopped=false
            if (d.live && !inRoom) tunedAtElapsedMs = SystemClock.elapsedRealtime()
            if (inRoom) {
                // One shared stream: its time zero is start_position_ms into the title, so the player starts
                // at the room's position minus that. A member's own stream covers the whole title.
                val stream = d.roomStream()
                playingStream = stream
                _state.update { it.copy(streamStartMs = stream?.startMs ?: 0, titleDurationMs = d.durationMs?.takeIf { len -> stream != null && len > 0 }) }
                // Until the socket has said where the room is, the descriptor's resume position is the room's.
                // A room this TV has just started on its own stream starts where the TV was: the room is put there next.
                val carried = carryOver?.positionMs?.takeIf { stream == null }
                player.play(d, reportOwner, RoomSync.playerPosition(carried ?: roomPosition() ?: d.resumePositionMs, stream))
                // A new room is paused at the start; until the room says "playing" this member waits.
                if (roomClient?.state?.value?.clock?.playing != true) player.pause()
            } else if (currentRequest?.bufferAt != null) {
                check(lease != null) { "Buffered position expired." }
                val requested = Instant.parse(currentRequest!!.bufferAt)
                val result = buffer.seek(requested)
                currentCoroutineContext().ensureActive()
                check(sessions.current().catalogScope() == owner) { "Playback scope changed." }
                player.play(d.copy(url = result.url), reportOwner)
                lastBufferSeek = requested
                _state.update { it.copy(bufferWindow = result.window, bufferTimeshift = true) }
            } else {
                val origin = d.startPositionMs?.takeIf { d.mode == PlaybackMode.HLS && !d.live }
                _state.update { it.copy(streamStartMs = origin ?: 0, titleDurationMs = d.durationMs?.takeIf { origin != null }) }
                val position = ((startPositionMs ?: d.resumePositionMs) - (origin ?: 0)).coerceAtLeast(0)
                player.play(d, reportOwner, position)
            }
            _state.update { it.copy(bufferWindow=buffer.window,bufferBusy=false) }
            if(lease != null) bufferHeartbeat=viewModelScope.launch {
                try {
                    while(true) {
                        delay(30_000)
                        if(sessions.current().catalogScope()!=owner) throw CancellationException("Playback scope changed")
                        val exo = player.exoPlayer
                        val at = bufferPlayhead()
                        val window=buffer.heartbeat(paused=!exo.playWhenReady, positionAt=at)
                        currentCoroutineContext().ensureActive()
                        if(sessions.current().catalogScope()!=owner || buffer.leaseId!=lease.leaseId) return@launch
                        _state.update { it.copy(bufferWindow=window) }
                    }
                } catch(e:CancellationException) { throw e }
                catch(_:Exception) {
                    if(buffer.leaseId==lease.leaseId) {
                        bufferHeartbeat=null
                        player.stop(); currentStopped=true
                        withContext(NonCancellable) { buffer.close(lease.leaseId) }
                        if(buffer.leaseId==null && _state.value.descriptor==d) _state.update { it.copy(bufferWindow=null,bufferBusy=false,bufferTimeshift=false,bufferError="Buffer lease expired or could not renew. Playback stopped. Choose Play/Retry to start again.",overlay=Overlay.Buffer) }
                    }
                }
            }
        } catch(e:CancellationException) {
            acquiredLeaseId?.let { id -> withContext(NonCancellable) { buffer.close(id) } }; throw e
        } catch(error:Exception) {
            acquiredLeaseId?.let { id -> withContext(NonCancellable) { buffer.close(id) } }
            currentCoroutineContext().ensureActive()
            if(sessions.current().catalogScope()!=owner) return
            if (error is ApiException && error.status in setOf(401, 403)) {
                _state.update { it.copy(bufferBusy = false) }
                streamRefused(error)
            }
            else _state.update { it.copy(bufferBusy=false, bufferError="Could not open the selected playback position. Retry playback.", overlay=Overlay.Buffer) }
        }
    }

    internal fun localRewindRange(): WatchedTimeline? {
        val tune = tunedAtElapsedMs ?: return null
        val shown = _state.value
        if (seat != null || !shown.live || shown.bufferTimeshift || shown.failure != null || shown.audioChoice != null ||
            player.state.value.audioBlocked || currentStopped) return null
        val exo = player.exoPlayer
        if (!exo.isCurrentMediaItemSeekable || exo.currentTimeline.isEmpty) return null
        val window = exo.currentTimeline.getWindow(exo.currentMediaItemIndex, androidx.media3.common.Timeline.Window())
        return localTuneWindow(window.positionInFirstPeriodMs, exo.duration, SystemClock.elapsedRealtime() - tune)
    }

    internal fun localPlayhead(range: WatchedTimeline): Instant =
        range.clamp(range.end.minusMillis((player.exoPlayer.duration - player.exoPlayer.currentPosition).coerceAtLeast(0)))

    internal fun seekLocal(tune: String?, at: Instant?) {
        if (tune == null || tune != _state.value.descriptor?.url) return
        val range = localRewindRange() ?: return
        if (at == null) player.exoPlayer.seekToDefaultPosition()
        else player.seekTo(player.exoPlayer.duration - java.time.Duration.between(range.clamp(at), range.end).toMillis())
        hideOverlay()
        showOsd()
    }

    /** A timestamp on this tune's media timeline. A playing live edge follows the growing buffer. */
    internal fun bufferPlayhead(): Instant? {
        val exo = player.exoPlayer
        if (buffer.leaseId == null || (!_state.value.bufferTimeshift && exo.playWhenReady)) return null
        val timeline = exo.currentTimeline
        val start = if (!timeline.isEmpty) timeline.getWindow(exo.currentMediaItemIndex,
            androidx.media3.common.Timeline.Window()).windowStartTimeMs else androidx.media3.common.C.TIME_UNSET
        return if (start != androidx.media3.common.C.TIME_UNSET) Instant.ofEpochMilli(start + exo.currentPosition)
            else lastBufferSeek
    }

    fun openBuffer() { _state.update { it.copy(overlay=Overlay.Buffer) }; refreshBuffer() }
    fun refreshBuffer() {
        if(buffer.leaseId==null) return
        bufferSeekJob?.cancel()
        val lease=buffer.leaseId
        bufferSeekJob=viewModelScope.launch {
            val owner=sessions.current().catalogScope()
            try {
                val window=buffer.heartbeat(paused=!player.exoPlayer.playWhenReady, positionAt=bufferPlayhead())
                currentCoroutineContext().ensureActive()
                if(buffer.leaseId==lease && sessions.current().catalogScope()==owner) _state.update { it.copy(bufferWindow=window,bufferError=null) }
            } catch(e:CancellationException) { throw e }
            catch(_:Exception) { currentCoroutineContext().ensureActive(); if(buffer.leaseId==lease) _state.update { it.copy(bufferError="Could not refresh the retained window. Go Live or retry.") } }
        }
    }

    fun seekBuffer(at: Instant?) {
        if(buffer.leaseId==null) return
        bufferSeekJob?.cancel()
        val descriptor=_state.value.descriptor ?: return
        val lease=buffer.leaseId
        _state.update { it.copy(bufferBusy=true,bufferError=null) }
        bufferSeekJob=viewModelScope.launch {
            val reportOwner=sessions.current()
            val owner=reportOwner.catalogScope()
            try {
                val result=buffer.seek(at)
                currentCoroutineContext().ensureActive()
                if(buffer.leaseId!=lease || sessions.current().catalogScope()!=owner) return@launch
                lastBufferSeek = at
                player.play(descriptor.copy(url=result.url),reportOwner)
                _state.update { it.copy(bufferWindow=result.window,bufferBusy=false,bufferTimeshift=at!=null,overlay=Overlay.Osd) }
                showOsd()
            } catch(e:CancellationException) { throw e }
            catch(_:Exception) { currentCoroutineContext().ensureActive(); if(buffer.leaseId==lease) _state.update { it.copy(bufferBusy=false,bufferError="That position expired or could not be loaded. Choose Go Live or refresh the retained window.") } }
        }
    }

    private suspend fun loadChannelList(d: PlaybackDescriptor) {
        val ctx = d.channelContext
        val list = channelCache.resolve(ctx?.listId, d.id)
        // The server's index counts in its own list; in any other it is another channel's row.
        val idx = list.indexOfFirst { it.id == d.id }
        _state.update { it.copy(channels = list, index = idx, osdChannel = list.getOrNull(idx)) }
    }

    // ---- OSD -----------------------------------------------------------------------------------

    /** Films, catch-up and buffered history are driven from the control row; only true live gets the banner. */
    private fun usesControls(s: PlayerScreenState = _state.value) = !s.live || s.bufferTimeshift

    fun showOsd(focus: ControlsFocus = ControlsFocus.PlayPause) {
        if (_state.value.overlay in listOf(Overlay.MiniList, Overlay.Menu, Overlay.Rewind, Overlay.Buffer)) return
        if (usesControls()) { showControls(focus); return }
        _state.update { it.copy(overlay = Overlay.Osd) }
        osdJob?.cancel()
        osdJob = viewModelScope.launch {
            delay(OSD_HIDE_MS)
            if (_state.value.numberEntry.isEmpty()) _state.update { if (it.overlay == Overlay.Osd) it.copy(overlay = Overlay.None) else it }
        }
    }

    // ---- Film controls ------------------------------------------------------------------------

    fun showControls(focus: ControlsFocus = ControlsFocus.PlayPause) {
        if (_state.value.overlay in listOf(Overlay.MiniList, Overlay.Menu, Overlay.Rewind, Overlay.Buffer)) return
        // Focus is placed when the row appears; a row already up keeps where the viewer is.
        _state.update { if (it.overlay == Overlay.Controls) it else it.copy(overlay = Overlay.Controls, controlsFocus = focus) }
        armControlsHide()
    }

    /** Any key press while the row is up counts as activity. */
    fun touchControls() { if (_state.value.overlay == Overlay.Controls) armControlsHide() }

    private fun armControlsHide() {
        osdJob?.cancel()
        osdJob = viewModelScope.launch {
            delay(CONTROLS_HIDE_MS)
            // A paused picture keeps its controls: they are the only sign of why it stopped. An open
            // audio or subtitle list keeps them too.
            while (!player.exoPlayer.playWhenReady || _state.value.picker != null) delay(500)
            _state.update { if (it.overlay == Overlay.Controls) it.copy(overlay = Overlay.None) else it }
        }
    }

    /**
     * Left/Right on the scrubber and the ±10 s buttons. The target moves at once so the bar follows
     * the remote; the seek itself waits for the presses to settle, so a held key is one seek, not twenty.
     */
    fun scrubBy(deltaMs: Long) {
        val ps = player.state.value
        val client = roomClient
        if (seat != null) {
            val room = client?.state?.value
            val clock = room?.clock
            when {
                room == null || room.connection != RoomConnection.Connected -> { flash(ROOM_RECONNECTING); return }
                room.controlsLocked && !room.isHost -> { flash(ROOM_HOST_ONLY); return }
                // Nothing to seek from until the room has said where it is.
                clock == null -> return
                clock.live -> { flash(roomRefusalText("live_no_seek").orEmpty()); return }
            }
        }
        val shown = _state.value
        val duration = shown.titleDurationMs(ps.durationMs)
        if (!shown.canSeek(ps.seekable) || duration <= 0) return
        // Any seek dismisses the Up next card; it comes back at the very end.
        dismissUpNext()
        // In a room the bar starts from where the room is, not from where this player happens to be.
        val target = ((shown.scrubTargetMs ?: roomPosition() ?: shown.titlePositionMs(ps.positionMs)) + deltaMs).coerceIn(0, duration)
        _state.update { it.copy(scrubTargetMs = target) }
        scrubJob?.cancel()
        scrubJob = viewModelScope.launch {
            delay(SCRUB_SETTLE_MS)
            // The room's answer (a state message) moves every member, this one included.
            if (client != null) client.seek(target) else seekTitle(target)
            // Keep showing the target until the player's position ticker has caught up with it.
            delay(SCRUB_RELEASE_MS)
            _state.update { it.copy(scrubTargetMs = null) }
        }
    }

    private fun seekTitle(positionMs: Long) {
        if (currentRequest?.preferHls == true) retryAt(positionMs)
        else player.seekTo(positionMs)
    }

    fun startOver() {
        dismissUpNext()
        scrubJob?.cancel()
        _state.update { it.copy(scrubTargetMs = null) }
        if (seat != null) roomControl { it.seek(0) } else seekTitle(0)
    }

    // ---- Skip intro / credits, Up next, Still watching (UpNextFlow) -----------------------------

    /** The profile's player preferences: `autoplay_next`, then `back_minimises`. A failed read keeps the defaults. */
    private suspend fun loadPlaybackPrefs() {
        val profile = sessions.current().profile?.id ?: return
        val document = try { prefs.readAll(profile) }
        catch (e: CancellationException) { if (currentCoroutineContext().isActive) return else throw e }
        catch (_: Exception) { return }
        val captions = (document["captions.v1"] as? JsonObject)?.let { raw ->
            fun field(key: String) = (raw[key] as? JsonPrimitive)?.contentOrNull
            val size = field("size") ?: return@let null
            val background = field("background") ?: return@let null
            CaptionAppearance(size, field("color") == "yellow", background)
        }
        _state.update { it.copy(autoplayNext = autoplayNextPref(document[AUTOPLAY_NEXT_PREF]),
            backMinimises = backMinimisesPref(document[BACK_MINIMISES_PREF]), captions = captions) }
    }

    /** "Back shrinks to mini player" in the settings: applies at once and is stored in the profile's preferences. */
    fun setBackMinimises(on: Boolean) {
        val before = _state.value.backMinimises
        if (before == on) return
        _state.update { it.copy(backMinimises = on) }
        viewModelScope.launch {
            val profile = sessions.current().profile?.id ?: return@launch
            try { prefs.write(profile, BACK_MINIMISES_PREF, JsonPrimitive(on)) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { _state.update { it.copy(backMinimises = before) }; flash("Couldn't save that setting. Try again.") }
        }
    }

    /** "Autoplay next episode" in the settings: applies at once and is stored in the profile's preferences. */
    fun setAutoplayNext(on: Boolean) {
        val before = _state.value.autoplayNext
        if (before == on) return
        _state.update { it.copy(autoplayNext = on) }
        viewModelScope.launch {
            val profile = sessions.current().profile?.id ?: return@launch
            try { prefs.write(profile, AUTOPLAY_NEXT_PREF, JsonPrimitive(on)) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { _state.update { it.copy(autoplayNext = before) }; flash("Couldn't save that setting. Try again.") }
        }
    }

    /** A key press (or a mini-player command): somebody is watching, so autoplay may carry on. */
    fun userInteracted() { unattendedAutoplays = 0 }

    /** Four times a second: where the title is, what is shown for it, and whether the countdown ran out. */
    private fun flowTick() {
        val s = _state.value
        val plan = s.upNext
        if (plan.kind == UpNextFlow.Kind.None) return
        if (s.advancing || s.stillWatching || s.failure != null || s.retrying || currentStopped) return
        val ps = player.state.value
        val position = s.scrubTargetMs ?: s.titlePositionMs(ps.positionMs)
        val duration = s.titleDurationMs(ps.durationMs).takeIf { it > 0 } ?: s.descriptor?.durationMs ?: 0L
        val look = UpNextFlow.Look(SystemClock.elapsedRealtime(), position, duration, ps.isPlaying, ps.ended)
        val (next, effect) = UpNextFlow.observe(flow, plan, look, s.autoplayNext)
        flow = next
        publishFlow(position)
        if (effect == UpNextFlow.Effect.CountdownDone) {
            val target = plan.next ?: return
            if (UpNextFlow.asksStillWatching(unattendedAutoplays)) {
                player.pause()
                _state.update { it.copy(stillWatching = true, upNextCard = null, skipCredits = false, overlay = Overlay.None, picker = null) }
            } else playEpisode(target, auto = true, forward = true)
        }
    }

    /** What [flow] shows at [positionMs]: the skip buttons and the card. */
    private fun publishFlow(positionMs: Long) {
        val s = _state.value
        val plan = s.upNext
        val card = plan.next?.takeIf { UpNextFlow.cardShowing(flow) }?.let { next ->
            UpNextCardModel(
                next,
                secondsLeft = if (s.autoplayNext) UpNextFlow.secondsLeft(flow) else null,
                progress = (flow.countedMs.toFloat() / UpNextFlow.COUNTDOWN_MS).coerceIn(0f, 1f),
            )
        }
        val intro = UpNextFlow.skipIntroTarget(plan, positionMs)
        val credits = UpNextFlow.skipCreditsVisible(plan, flow, positionMs)
        _state.update { it.copy(skipIntroTo = intro, skipCredits = credits, upNextCard = card) }
    }

    /** Back, "Keep watching" or a seek: the card goes until the very end. */
    fun dismissUpNext() {
        if (!UpNextFlow.cardShowing(flow)) return
        flow = UpNextFlow.dismiss(flow)
        val s = _state.value
        publishFlow(s.scrubTargetMs ?: s.titlePositionMs(player.state.value.positionMs))
    }

    fun skipIntro() {
        val target = _state.value.skipIntroTo ?: return
        scrubJob?.cancel()
        _state.update { it.copy(scrubTargetMs = null, skipIntroTo = null) }
        seekTitle(target)
    }

    /** Skip credits: the next episode, or (a film, the last episode) the end of playback, reported watched. */
    fun skipCredits() {
        val plan = _state.value.upNext
        when (plan.kind) {
            UpNextFlow.Kind.Episode -> plan.next?.let { playEpisode(it, auto = false, forward = true) }
            UpNextFlow.Kind.EndsPlayback -> viewModelScope.launch { finishTitle() }
            UpNextFlow.Kind.None -> Unit
        }
    }

    /**
     * OK while the controls are hidden: the visible skip button (Skip intro, else Skip credits), or the
     * Up next card's "Play next". False when nothing of that is up, so OK does what it always did.
     */
    fun activateVisibleSkip(): Boolean {
        val s = _state.value
        return when {
            s.skipIntroTo != null -> { skipIntro(); true }
            s.skipCredits -> { skipCredits(); true }
            s.upNextCard != null -> { playNextEpisode(); true }
            else -> false
        }
    }

    fun playNextEpisode() { _state.value.upNext.next?.let { playEpisode(it, auto = false, forward = true) } }
    fun playPreviousEpisode() { _state.value.upNext.previous?.let { playEpisode(it, auto = false, forward = false) } }

    fun stillWatchingContinue() {
        _state.update { it.copy(stillWatching = false) }
        unattendedAutoplays = 0
        val next = _state.value.upNext.next
        if (next != null) playEpisode(next, auto = false, forward = true) else player.resume()
    }

    fun stillWatchingBack() {
        _state.update { it.copy(stillWatching = false) }
        _exit.tryEmit(Unit)
    }

    /** The title is done (Skip credits on a film): reported watched, and the player closes. */
    private suspend fun finishTitle() {
        attachJob?.cancel()
        player.stop(completed = true); currentStopped = true
        withContext(NonCancellable) { buffer.close() }
        _exit.emit(Unit)
    }

    /**
     * Plays [target] (the next or previous episode) in this player: resolved the way Play resolves it,
     * with the audio language chosen now carried over. Moving on from the credits or the end reports
     * this episode watched. A failure leaves the current episode playing and says so.
     */
    private fun playEpisode(target: UpNext, auto: Boolean, forward: Boolean) {
        if (seat != null || _state.value.advancing) return
        val s = _state.value
        val ps = player.state.value
        val duration = s.titleDurationMs(ps.durationMs).takeIf { it > 0 } ?: s.descriptor?.durationMs ?: 0L
        val completes = forward && UpNextFlow.finishesCurrent(s.upNext, s.titlePositionMs(ps.positionMs), duration, ps.ended)
        val language = ps.audioTracks.firstOrNull { it.selected && it.supported }?.language?.takeIf { it.isNotBlank() && !it.equals("und", true) }
        flow = UpNextFlow.State(phase = UpNextFlow.Phase.Done)
        scrubJob?.cancel()
        _state.update { it.copy(advancing = true, upNextCard = null, skipCredits = false, skipIntroTo = null, stillWatching = false, scrubTargetMs = null, message = "Loading ${target.title.ifBlank { "the next episode" }}…") }
        rewindJob?.cancel()
        attachJob?.cancel()
        attachJob = viewModelScope.launch {
            val owner = sessions.current().catalogScope()
            try {
                val result = audioPlaybackAttempt(PlaybackRequest(target.id), true, api::playbackOptions, api::resolve)
                currentCoroutineContext().ensureActive()
                if (sessions.current().catalogScope() != owner) return@launch
                val ready = result as? AudioPlaybackAttempt.Ready ?: throw IllegalStateException("No stream")
                val d = language?.let { lang -> ready.descriptor.copy(preferredAudioLanguages = listOf(lang) + ready.descriptor.preferredAudioLanguages.filterNot { it.equals(lang, true) }) } ?: ready.descriptor
                // Before start() stops it: its last report says "watched", not where the credits were.
                if (completes) player.stop(completed = true)
                currentRequest = ready.request; failedRequest = null
                pausedRetry.reset()
                unattendedAutoplays = if (auto) unattendedAutoplays + 1 else 0
                _state.update { it.copy(advancing = false, message = null) }
                start(d)
            } catch (e: CancellationException) {
                _state.update { it.copy(advancing = false, message = null) }
                throw e
            } catch (e: Exception) {
                // The card does not come straight back: the episode plays on to its end, where it does.
                flow = UpNextFlow.State(phase = UpNextFlow.Phase.Dismissed)
                _state.update { it.copy(advancing = false, message = null) }
                flash("Couldn't start ${target.title.ifBlank { "the next episode" }}. ${ApiErrors.describe(e).title}.")
            }
        }
    }

    // ---- Audio and subtitle lists (control row) ------------------------------------------------

    fun openPicker(picker: TrackPicker) { _state.update { it.copy(picker = picker) }; armControlsHide() }
    fun closePicker() { _state.update { it.copy(picker = null) }; if (_state.value.overlay == Overlay.Controls) armControlsHide() }

    // ---- Play / pause (the room's, when in one) -------------------------------------------------

    fun togglePlayPause() {
        if (seat == null) { player.togglePlayPause(); return }
        if (liveRoom()) { flash(roomRefusalText("live_no_pause").orEmpty()); return }
        val playing = roomClient?.state?.value?.clock?.playing ?: player.exoPlayer.playWhenReady
        roomControl { if (playing) it.pause() else it.play() }
    }
    fun resumePlayback() { if (seat == null) player.resume() else roomControl { it.play() } }
    fun pausePlayback() {
        when {
            seat == null -> player.pause()
            liveRoom() -> flash(roomRefusalText("live_no_pause").orEmpty())
            else -> roomControl { it.pause() }
        }
    }

    /** A live room plays on for everyone: a pause would leave this member behind the others for good. */
    private fun liveRoom(): Boolean = roomClient?.state?.value?.clock?.live == true

    // ---- Watch room ----------------------------------------------------------------------------

    val inRoom: Boolean get() = seat != null

    /** Where the room is now, when in a room that has a position (not a live one) and has said so. */
    private fun roomPosition(): Long? {
        val client = roomClient ?: return null
        return client.state.value.clock?.let { RoomSync.positionAt(it, client.serverNowMs()) }
    }

    suspend fun roomControls(action: String? = null, ref: String = "") =
        rooms.controls(requireNotNull(seat), action, ref)

    suspend fun roomSuggestions(query: String) = api.search(query, false).playHere.take(6)

    private fun startRoom(s: RoomSeat, isHost: Boolean = false) {
        var seenRoomRevision = s.titleRevision
        val client = rooms.client(s, isHost)
        roomClient = client
        roomJob?.cancel()
        _state.update { it.copy(roomCode = s.code, room = client.state.value, roomEnded = null) }
        roomJob = viewModelScope.launch {
            // Cancelling this job is how the room is left: run() gives the seat back on its way out.
            launch { client.run() }
            launch { val url = sessions.current().serverUrl?.let { roomShareUrl(it, s.code) }; _state.update { it.copy(roomShareUrl = url) } }
            carryOver?.let { carry -> launch { carryInto(client, carry) } }
            launch {
                client.state.collect { room ->
                    val previous = seenRoomRevision
                    val revision = room.titleRevision
                    if (revision != null) seenRoomRevision = revision
                    _state.update { it.copy(room = room) }
                    if (revision != null && previous != revision) {
                        player.pause()
                        recoverRoom(auto = true)
                        return@collect
                    }
                    when {
                        room.connection == RoomConnection.Closed -> roomClosed(room.closedReason)
                        // A seek restarted the room's stream: what is playing is a start that no longer exists.
                        RoomSync.streamStep(playingStream, room.stream) == StreamStep.Reload -> followEpoch()
                    }
                }
            }
            launch {
                // This member lost its place: the stream address expired, or the segments are those of a
                // start the room has left behind. Asked for again a few times before the viewer is.
                // A refusal of the stream is not a lost place: it has its own answer (see streamRefused).
                player.state.map { it.error != null && it.refusal == null && it.reconnectAttempt == 0 && !it.audioBlocked }.distinctUntilChanged().collect { failed ->
                    if (failed && _state.value.roomEnded == null && autoRecoveries < MAX_AUTO_RECOVERIES) { autoRecoveries++; recoverRoom(auto = true) }
                }
            }
            launch {
                // A live room's player reconnects by itself, with the address it has. When that keeps failing
                // the address is the likelier fault: fetched again once per run of attempts.
                player.state.map { it.reconnectAttempt }.distinctUntilChanged().collect { attempt ->
                    if (attempt == LIVE_ROOM_REFETCH_ATTEMPT && _state.value.roomEnded == null) recoverRoom(auto = true)
                }
            }
            launch { player.state.map { it.isPlaying }.distinctUntilChanged().collect { if (it) autoRecoveries = 0 } }
            launch { client.refusals.collect { refusal -> roomRefusalText(refusal.code)?.let(::flash) } }
            launch {
                // Buffering start/stop, and again after every reconnect: the room pauses for a member who stalls.
                combine(
                    player.state.map { it.buffering }.distinctUntilChanged(),
                    client.state.map { it.connection == RoomConnection.Connected }.distinctUntilChanged(),
                ) { buffering, connected -> buffering to connected }.collect { (buffering, connected) -> if (connected) client.buffering(buffering) }
            }
            launch { keepInStep(client) }
        }
    }

    /** Applies [RoomSync] twice a second: the room clock decides, this player follows. */
    private suspend fun keepInStep(client: RoomClient) {
        var lastSeekAt = 0L
        var seekedFor: RoomClock? = null
        val live = LiveSteering()
        while (true) {
            delay(ROOM_SYNC_TICK_MS)
            val room = client.state.value
            val clock = room.clock ?: continue
            val s = _state.value
            // A room this TV just started is put where the TV was first: following its untouched start would jump back to zero.
            if (room.connection != RoomConnection.Connected || currentStopped || s.failure != null || s.retrying || carryOver != null) continue
            val exo = player.exoPlayer
            if (exo.playbackState == Player.STATE_IDLE || exo.playbackState == Player.STATE_ENDED || player.state.value.audioBlocked) continue
            val stream = playingStream
            val wanted = when (RoomSync.streamStep(stream, room.stream)) {
                // Positions on a start of the stream the room has left mean nothing: load the new one first.
                StreamStep.Reload -> { followEpoch(); continue }
                // The descriptor is from a later start than the last state: play and pause still apply,
                // the position waits for the state that belongs to this stream.
                StreamStep.Wait -> clock.copy(positionMs = null)
                StreamStep.Same -> clock
            }
            if (clock.live) { steerLive(live); continue }
            when (val action = RoomSync.decide(wanted, client.serverNowMs(), exo.currentPosition, exo.playWhenReady, stream)) {
                SyncAction.None -> Unit
                SyncAction.Play -> player.resume()
                SyncAction.Pause -> player.pause()
                is SyncAction.SeekTo -> {
                    val now = SystemClock.elapsedRealtime()
                    val canSeek = exo.isCurrentMediaItemSeekable && exo.duration > 0
                    // A playing room's position depends on the server clock: wait for the first pong.
                    val trusted = client.clockSynced || !clock.playing
                    // Once per room state at once; after that only from a settled player, so a member who is
                    // still buffering towards the last target is not sent chasing a newer one every tick.
                    val due = clock != seekedFor || (exo.playbackState == Player.STATE_READY && now - lastSeekAt >= ROOM_SEEK_COOLDOWN_MS)
                    when {
                        s.scrubTargetMs != null -> Unit
                        canSeek && trusted && due -> { player.seekTo(action.positionMs); lastSeekAt = now; seekedFor = clock }
                        // A stream that cannot be seeked can still stop and start with the room.
                        !canSeek && exo.playbackState == Player.STATE_READY && clock.playing != exo.playWhenReady ->
                            if (clock.playing) player.resume() else player.pause()
                    }
                }
            }
        }
    }

    /** What [steerLive] remembers between ticks. */
    private class LiveSteering {
        /** Holding the picture for the reserve, since this time (0: not holding). */
        var holdingSince = 0L
        /** The hold may start once per start or stall of the player, as the browser's reserve does. */
        var holdArmed = true
        var wasReady = false
        /** First seen well behind the others at this time (0: not behind). */
        var laggingSince = 0L
    }

    /**
     * A live room: no position to follow, and the room never pauses (see [RoomSync.liveStep]). After a
     * start or a stall the picture holds until the reserve every member keeps is in hand; a member found
     * well behind the others for a couple of seconds starts the channel again at the edge.
     */
    private fun steerLive(live: LiveSteering) {
        val exo = player.exoPlayer
        val now = SystemClock.elapsedRealtime()
        val ready = exo.playbackState == Player.STATE_READY
        if (ready && !live.wasReady) live.holdArmed = true
        live.wasReady = ready
        if (!ready) { live.laggingSince = 0L; return }
        val ahead = (exo.bufferedPosition - exo.currentPosition).coerceAtLeast(0)
        val holding = live.holdingSince != 0L
        when (RoomSync.liveStep(ahead, holding)) {
            LiveStep.CatchUp -> when {
                live.laggingSince == 0L -> live.laggingSince = now
                now - live.laggingSince >= LIVE_LAG_SUSTAIN_MS -> {
                    live.laggingSince = 0L; live.holdingSince = 0L
                    player.rejoinLive()
                }
            }
            // A feed slower than real time never builds the reserve: it plays what it has after a while.
            LiveStep.Hold -> {
                live.laggingSince = 0L
                if (holding && now - live.holdingSince >= LIVE_HOLD_MAX_MS) { live.holdingSince = 0L; live.holdArmed = false; player.resume() }
                else if (!holding && live.holdArmed) { live.holdingSince = now; exo.playWhenReady = false }
                else if (!holding && !exo.playWhenReady) player.resume()
            }
            LiveStep.Stay -> {
                live.laggingSince = 0L
                if (holding) { live.holdingSince = 0L; live.holdArmed = false }
                if (!exo.playWhenReady) player.resume()
            }
        }
    }

    // ---- Watch together: starting a room from what is playing --------------------------------------

    /** What "Watch together" would open a room on now; null when the menu does not offer it. */
    fun roomStart(s: PlayerScreenState = _state.value): RoomStart? =
        RoomStartRules.roomStart(s.descriptor, currentRequest, s.mayStartRoom, inRoom = seat != null || s.roomCode != null)

    fun startWatchTogether() {
        val start = roomStart() ?: return
        hideOverlay()
        together.open(start.titleId, start.title, start.subject, start.variantId, start.audioTrack)
    }
    fun startTogetherRoom() = together.start()
    fun retryTogetherRoom() = together.retry()
    fun cancelTogetherRoom() { together.cancel(); showOsd() }

    /**
     * Start in the "Watch together" dialog: this playback becomes the host's seat in the room it just
     * made, as a joined room plays (the room clock, RoomSync, the room's controls), without a second
     * player. This player's own stream is given back first, since a one-connection source would
     * refuse the room's; when the seat cannot be taken, that stream is resolved again where it was
     * and the failure is the dialog's to show.
     */
    private suspend fun switchIntoRoom(room: Room, allowUnverifiedAudio: Boolean) {
        if (seat != null) return
        val d = _state.value.descriptor
        val exo = player.exoPlayer
        val wasPlaying = exo.playWhenReady
        // A film carries on where it is; a channel has no position, and a live room plays on its own.
        val positionMs = if (d != null && !d.live && !room.live) _state.value.titlePositionMs(exo.currentPosition).takeIf { it > 0 } else null
        zapJob?.cancel(); numberJob?.cancel(); rewindJob?.cancel(); scrubJob?.cancel()
        feedWatch = null; feedNotice = null; failedRequest = null
        stopCurrentPlayback()
        val joined = try {
            rooms.joinInto(key, room.code, allowUnverifiedAudio)
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            playOwnAgain(positionMs)
            throw e
        }
        seat = joined
        playingStream = joined.descriptor.roomStream()
        autoRecoveries = 0; epochMisses = 0
        carryOver = CarryOver(positionMs, wasPlaying)
        _state.update { it.copy(failure = null, failureKeepsPlaying = false, scrubTargetMs = null, overlay = Overlay.None, live = joined.descriptor.live) }
        startRoom(joined, isHost = room.isHost)
        attachJob = viewModelScope.launch {
            start(joined.descriptor)
            if (joined.descriptor.live) loadChannelList(joined.descriptor)
            showOsd()
        }
    }

    /** Sends what [CarryOverRules] decides once the new room has said where it is, then lets RoomSync take over. */
    private suspend fun carryInto(client: RoomClient, carry: CarryOver) {
        try {
            val room = client.state.first { CarryOverRules.step(carry, it) != CarryStep.Wait || it.connection == RoomConnection.Closed }
            val step = CarryOverRules.step(carry, room)
            if (step is CarryStep.Send) {
                step.seekToMs?.let { client.seek(it) }
                if (step.play) client.play()
                // RoomSync takes over once the room has answered; before that it would follow the untouched start.
                withTimeoutOrNull(CARRY_SETTLE_MS) { client.state.first { CarryOverRules.step(carry, it) !is CarryStep.Send } }
            }
        } finally {
            if (carryOver === carry) carryOver = null
        }
    }

    /** Playback before the room, again: the accepted request resolved afresh (its old address may have expired). */
    private fun playOwnAgain(resumeAt: Long?) {
        val request = currentRequest ?: _state.value.descriptor?.let { PlaybackRequest(it.id) } ?: return
        attachJob?.cancel()
        _state.update { it.copy(retrying = true) }
        attachJob = viewModelScope.launch {
            val owner = sessions.current().catalogScope()
            try {
                val fresh = coordinator.resolveAgain(request)
                currentCoroutineContext().ensureActive()
                if (sessions.current().catalogScope() != owner) return@launch
                start(fresh, resumeAt)
                if (fresh.live) loadChannelList(fresh)
                _state.update { it.copy(retrying = false) }
            } catch (e: CancellationException) {
                _state.update { it.copy(retrying = false) }
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(retrying = false, failure = ApiErrors.describe(e), failureKeepsPlaying = false) }
            }
        }
    }

    /** Play, pause and seek in a room: refused here when the room would refuse them, sent otherwise. */
    private fun roomControl(send: suspend (RoomClient) -> Boolean) {
        val client = roomClient ?: return
        val room = client.state.value
        when {
            room.controlsLocked && !room.isHost -> flash(ROOM_HOST_ONLY)
            room.connection != RoomConnection.Connected -> flash(ROOM_RECONNECTING)
            else -> viewModelScope.launch { if (!send(client)) flash(ROOM_RECONNECTING) }
        }
    }

    /** Host: only the host's play, pause and seek apply while locked. */
    fun toggleRoomLock() {
        val client = roomClient ?: return
        val room = client.state.value
        if (!room.isHost) { flash("Only the host can do that"); return }
        viewModelScope.launch { if (!client.lock(!room.controlsLocked)) flash(ROOM_RECONNECTING) }
    }

    /**
     * The room is on a later start of its shared stream than the one loaded here: load that one.
     * A descriptor that keeps coming back behind the room's `state` is not chased for ever; after a
     * few misses the viewer is asked, and "Try again" starts the count afresh.
     */
    private fun followEpoch() {
        val s = _state.value
        if (s.retrying || s.failure != null || s.roomEnded != null) return
        if (epochMisses < MAX_EPOCH_MISSES) { recoverRoom(auto = true); return }
        _state.update {
            it.copy(failure = UserError("Out of step with the room", "This TV couldn't load the room's stream after it was moved. Try again, or leave and join again with the room's code.", canRetry = true), failureKeepsPlaying = false)
        }
    }

    /**
     * The room's connection is over. When only this TV's seat or connection went, it joins again
     * with the member token it holds (the same seat, if the room still has it); otherwise, and when
     * that fails, the room is over for this TV and says why.
     */
    private fun roomClosed(reason: String?) {
        val now = SystemClock.elapsedRealtime()
        if (roomSeatRecoverable(reason) && (lastAutoRejoinAt == 0L || now - lastAutoRejoinAt >= AUTO_REJOIN_SPACING_MS)) {
            lastAutoRejoinAt = now
            recoverRoom(auto = true, rejoin = true, closedReason = reason)
        } else roomOver(reason)
    }

    private fun roomOver(reason: String?) {
        player.pause()
        _state.update { it.copy(roomEnded = roomClosedMessage(reason), leaveAsk = false, overlay = Overlay.None) }
    }

    fun askLeaveRoom() { if (seat != null && _state.value.roomEnded == null) _state.update { it.copy(leaveAsk = true, overlay = Overlay.None) } }
    fun stayInRoom() { _state.update { it.copy(leaveAsk = false) }; showOsd() }

    fun leaveRoom() {
        // A recovery on its way would take the seat up again.
        if (_state.value.retrying) attachJob?.cancel()
        roomJob?.cancel(); roomJob = null
        _state.update { it.copy(leaveAsk = false) }
        _exit.tryEmit(Unit)
    }

    /** Host: closes the room for everyone, then leaves. */
    fun endRoom() {
        val code = seat?.code ?: return
        _state.update { it.copy(leaveAsk = false) }
        viewModelScope.launch {
            try { rooms.close(code) }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { flash("Couldn't end the room. ${ApiErrors.describe(e).title}."); return@launch }
            roomJob?.cancel(); roomJob = null
            _exit.tryEmit(Unit)
        }
    }

    /** A short message over the picture, for the screen's own lists. */
    fun flashMessage(text: String) = flash(text)

    /** A short message over the picture. */
    private fun flash(text: String) {
        if (text.isBlank()) return
        messageJob?.cancel()
        _state.update { it.copy(message = text) }
        messageJob = viewModelScope.launch { delay(FLASH_MS); _state.update { if (it.message == text) it.copy(message = null) else it } }
    }

    fun hideOverlay() { rewindJob?.cancel(); bufferSeekJob?.cancel(); _state.update { it.copy(overlay = Overlay.None, rewindLoading = false, bufferBusy=false, audioChoice=null) } }
    fun openMiniList() { if (seat == null && _state.value.live && _state.value.channels.isNotEmpty()) _state.update { it.copy(overlay = Overlay.MiniList) } }
    suspend fun saveScene(token: String, name: String, position: Long) = api.saveScene(token, name, position)

    suspend fun reportRecordingIssue(id: String, category: String, position: Long) = api.reportRecordingIssue(id, category, position)
    fun openMenu() { _state.update { it.copy(overlay = Overlay.Menu) } }

    fun openRewind() {
        if(seat != null) return
        if(buffer.leaseId != null || localRewindRange() != null) { openBuffer(); return }
        val id = _state.value.descriptor?.id?.takeIf { it.startsWith("ch:") } ?: return
        rewindJob?.cancel()
        _state.update { it.copy(overlay = Overlay.Rewind, rewind = null, rewindLoading = true, rewindError = null) }
        rewindJob = viewModelScope.launch {
            val owner = sessions.current().catalogScope()
            try {
                val capability = api.rewindCapability(id)
                currentCoroutineContext().ensureActive()
                if (sessions.current().catalogScope() != owner || _state.value.descriptor?.id != id) return@launch
                _state.update { it.copy(rewind = capability, rewindLoading = false) }
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { _state.update { it.copy(rewindLoading = false, rewindError = "Could not load provider archive. Retry or return to live.") } }
        }
    }

    fun playArchive(at: Instant) = resolveChannelTime(at)
    fun goLive() { if(buffer.leaseId!=null) seekBuffer(null) else resolveChannelTime(null) }

    private fun resolveChannelTime(at: Instant?) {
        if(seat != null) return
        val id = _state.value.descriptor?.id?.takeIf { it.startsWith("ch:") } ?: return
        submitChannel(PlaybackRequest(id, rewindAt=at?.toString()), archiveAction=true)
    }

    fun cancelAudioChoice() { rewindJob?.cancel(); audioOwner=null; _state.update { it.copy(audioChoice=null, rewindLoading=false, overlay=Overlay.Osd, osdChannel=it.channels.getOrNull(it.index)) }; showOsd() }

    fun confirmAudioChoice(variantId: String, confirmed: Boolean) {
        val choice = _state.value.audioChoice ?: return
        val owner = audioOwner ?: return
        if(choice.options.options.none { it.variantId == variantId }) return
        _state.update { it.copy(audioChoice=null, overlay=Overlay.Osd) }
        submitChannel(choice.request.copy(variantId=variantId, allowUnverifiedAudio=confirmed), archiveAction=choiceArchiveAction, preflight=false, expectedOwner=owner)
    }

    private fun submitChannel(request: PlaybackRequest, archiveAction: Boolean=false, preflight: Boolean=true, expectedOwner: CatalogScope?=null) {
        rewindJob?.cancel()
        _state.update { it.copy(rewindLoading=true, rewindError=null, audioChoice=null) }
        rewindJob = viewModelScope.launch {
            val owner = sessions.current().catalogScope()
            if(expectedOwner != null && expectedOwner != owner) { cancelAudioChoice(); return@launch }
            val previousId = _state.value.descriptor?.id
            var previousStopped = false
            suspend fun stopForAcceptedPlayback() {
                currentCoroutineContext().ensureActive()
                if(sessions.current().catalogScope() != owner) throw CancellationException("Profile changed")
                stopCurrentPlayback(); previousStopped=true
            }
            try {
                val result = audioPlaybackAttempt(request, preflight, api::playbackOptions, { accepted ->
                    if(archiveAction) resolveArchiveSelection(accepted.id, accepted.rewindAt?.let(Instant::parse), owner.toString(), { sessions.current().catalogScope().toString() }, api::rewindCapability, api::resolve, accepted, ::stopForAcceptedPlayback)
                    else { stopForAcceptedPlayback(); api.resolve(accepted) }
                })
                currentCoroutineContext().ensureActive()
                if(sessions.current().catalogScope() != owner) return@launch
                when(result) {
                    is AudioPlaybackAttempt.Choice -> {
                        audioOwner=owner; choiceArchiveAction=archiveAction
                        _state.update { it.copy(audioChoice=result, rewindLoading=false, overlay=Overlay.None) }
                    }
                    is AudioPlaybackAttempt.Ready -> {
                        val descriptor=result.descriptor
                        // A feed switch names the channel it left once; Try again later is an ordinary resolve.
                        currentRequest=result.request.copy(replaces=null); failedRequest=null
                        // Another channel is another playback: its first stop is a first one.
                        pausedRetry.reset()
                        if(previousId != descriptor.id) lastChannelId=previousId
                        val idx=_state.value.channels.indexOfFirst { it.id == descriptor.id }
                        _state.update { it.copy(index=idx, osdChannel=it.channels.getOrNull(idx), rewindLoading=false, overlay=Overlay.Osd) }
                        start(descriptor)
                        if(descriptor.live) loadChannelList(descriptor)
                        showOsd()
                    }
                }
            } catch(e: CancellationException) { throw e }
            catch(_: ExpiredArchiveSelection) { _state.update { it.copy(rewindLoading=false, overlay=Overlay.Rewind, rewindError="That selection expired. Refresh the advertised window.") } }
            catch(e: Exception) {
                val rewindError=if(previousStopped) "Stream switch failed after stopping the previous stream. Select a channel or Go Live to retry." else "Could not check this source. The current stream was kept."
                // The archive dialog shows its own error line; a plain channel change gets the Try again dialog.
                if(archiveAction) _state.update { it.copy(rewindLoading=false, rewindError=rewindError) }
                else {
                    failedRequest=request
                    _state.update { it.copy(rewindLoading=false, rewindError=rewindError, failure=ApiErrors.describe(e), failureKeepsPlaying=!previousStopped, osdChannel=if(previousStopped) it.osdChannel else it.channels.getOrNull(it.index)) }
                }
            }
        }
    }

    // ---- Feed failover -------------------------------------------------------------------------

    /** The channel being watched for stalls, and the failover run it is part of; null when nothing is. */
    private var feedWatch: FeedFailover.Watch? = null
    /** Said once the feed a switch moved to is on screen (start() clears any message before that). */
    private var feedNotice: Pair<String, String>? = null

    /**
     * Looks at the picture once a second. Live at its edge only: not catch-up, a rewind, the local
     * buffer's past, nor a room. A stall of a channel that has played (or a feed switched to that does
     * not start) asks the server for the other feeds of its game and tunes to the next untried one.
     */
    private suspend fun watchFeeds() {
        var last: Long? = null
        var lastBuffered: Long? = null
        while (true) {
            delay(FEED_SAMPLE_MS)
            // A channel change is on its way: judge what it brings, not the stream it is replacing.
            if (rewindJob?.isActive == true || zapJob?.isActive == true) { last = null; lastBuffered = null; continue }
            val s = _state.value
            val d = s.descriptor
            val now = SystemClock.elapsedRealtime()
            val watching = feedWatch
            // A switch this run made whose resolve failed: the dialog is up for a channel that never started.
            val failedSwitch = watching != null && watching.tried.isNotEmpty() && failedRequest?.id == watching.channel && d?.id != watching.channel
            val eligible = seat == null && (failedSwitch || (d != null && d.live && s.live && d.id.startsWith("ch:") && !s.bufferTimeshift && !currentStopped))
            if (!eligible || d == null) { feedWatch = null; last = null; lastBuffered = null; continue }
            var w = if (failedSwitch) checkNotNull(watching) else watching?.takeIf { it.channel == d.id } ?: FeedFailover.watchChannel(d.id, now).also { last = null; lastBuffered = null }
            if (!failedSwitch && w.tried.isNotEmpty() && w.channel == d.id) feedNotice?.let { (from, to) -> feedNotice = null; flash(FeedFailover.notice(from, to)) }
            val exo = player.exoPlayer
            val position = exo.currentPosition
            val step = last?.let { position - it } ?: 0L
            last = position
            val progressing = !failedSwitch && exo.isPlaying && step in 1..MAX_FEED_STEP_MS
            // New video still coming in: the buffered position grew. A reconnecting (failed) stream is not delivering.
            val buffered = exo.bufferedPosition
            val grew = lastBuffered?.let { buffered > it } ?: false
            lastBuffered = buffered
            val arriving = !failedSwitch && grew && player.state.value.error == null
            // Paused by the viewer, held for an audio choice, or a refusal no other feed would get past.
            val paused = !failedSwitch && (!player.wantsToPlay || player.state.value.audioBlocked || s.failure != null || s.audioChoice != null)
            w = FeedFailover.observe(w, now, progressing, paused, arriving)
            feedWatch = w
            if (!FeedFailover.shouldSwitch(w, now)) continue
            val alternates = try { api.playbackAlternates(w.channel).alternates } catch (e: CancellationException) { throw e } catch (_: Exception) { emptyList() }
            if (feedWatch != w) continue  // the viewer moved on while the server was asked
            val nextId = FeedFailover.nextFeed(w, alternates.map { it.channelId })
            val next = alternates.firstOrNull { it.channelId == nextId }
            if (next == null) { feedWatch = FeedFailover.giveUp(w); continue }
            val fromName = s.channels.firstOrNull { it.id == w.channel }?.name ?: d.title.takeIf { d.id == w.channel && it.isNotBlank() } ?: "This channel"
            feedWatch = FeedFailover.watchSwitched(w, next.channelId, now)
            feedNotice = fromName to next.channelName
            last = null
            lastBuffered = null
            failedRequest = null
            _state.update { it.copy(failure = null, failureKeepsPlaying = false) }
            submitChannel(PlaybackRequest(next.channelId, replaces = w.channel))
        }
    }

    // ---- Zapping -------------------------------------------------------------------------------

    /** +1 = next channel in the list, -1 = previous. Wraps around. */
    fun zap(delta: Int) {
        val s = _state.value
        if (!s.live || s.channels.isEmpty() || seat != null) return
        rewindJob?.cancel(); audioOwner=null
        _state.update { it.copy(audioChoice=null, rewindLoading=false) }
        val from = s.channels.indexOfFirst { it.id == s.osdChannel?.id }.takeIf { it >= 0 } ?: s.index.coerceAtLeast(0)
        val target = Math.floorMod(from + delta, s.channels.size)
        _state.update { it.copy(osdChannel = s.channels[target]) }
        showOsd()
        zapJob?.cancel()
        zapJob = viewModelScope.launch {
            delay(ZAP_SETTLE_MS)
            tuneTo(s.channels[target])
        }
    }

    fun tuneTo(ch: Channel) {
        // A room stays on what it is watching together.
        if(seat != null) return
        if(ch.id == _state.value.descriptor?.id && _state.value.live && !currentStopped) { _state.update { it.copy(osdChannel=ch) }; return }
        submitChannel(PlaybackRequest(ch.id))
    }

    fun lastChannel() {
        val id = lastChannelId ?: return
        val ch = _state.value.channels.firstOrNull { it.id == id } ?: return
        tuneTo(ch)
    }

    val hasLastChannel: Boolean get() = lastChannelId != null

    // ---- Number entry --------------------------------------------------------------------------

    fun digit(d: Int) {
        if (!_state.value.live || seat != null) return
        val next = ChannelNumberEntry.append(_state.value.numberEntry, d)
        _state.update { it.copy(numberEntry = next, overlay = Overlay.Osd) }
        osdJob?.cancel()
        numberJob?.cancel()
        numberJob = viewModelScope.launch {
            delay(NUMBER_COMMIT_MS)
            commitNumber()
        }
    }

    fun commitNumber(): Boolean {
        val entry = _state.value.numberEntry
        val n = ChannelNumberEntry.number(entry) ?: return false
        numberJob?.cancel()
        _state.update { it.copy(numberEntry = "") }
        val ch = ChannelNumberEntry.match(entry, _state.value.channels) { it.number }
        if (ch != null) tuneTo(ch) else {
            _state.update { it.copy(message = "No channel $n") }
            viewModelScope.launch { delay(2000); _state.update { if (it.message == "No channel $n") it.copy(message = null) else it } }
        }
        showOsd()
        return true
    }

    // ---- Menu: aspect / sleep ------------------------------------------------------------------

    fun cycleAspect() = _state.update { it.copy(aspect = it.aspect.next()) }

    fun setSleep(minutes: Int?) {
        sleepJob?.cancel()
        _state.update { it.copy(sleepMinutes = minutes, sleepEndsAt = minutes?.let { m -> System.currentTimeMillis() + m * 60_000L }) }
        if (minutes != null) {
            sleepJob = viewModelScope.launch {
                delay(minutes * 60_000L)
                player.pause()
                _exit.tryEmit(Unit)
            }
        }
    }

    /**
     * "Try again". The descriptor on screen is never replayed: its stream URL carries a token that
     * has usually expired by the time playback fails, so the accepted request is resolved again
     * through the coordinator. A failed channel change repeats that change instead.
     */
    private fun playbackPosition(descriptor: PlaybackDescriptor): Long =
        player.exoPlayer.currentPosition.takeIf { it > 0 }?.let(_state.value::titlePositionMs) ?: descriptor.resumePositionMs

    fun retry() = retryAt(null)

    private fun retryAt(positionMs: Long?) {
        val zap = failedRequest
        if (zap != null) {
            failedRequest = null
            _state.update { it.copy(failure = null, message = null) }
            submitChannel(zap, archiveAction = zap.rewindAt != null)
            return
        }
        if (seat != null) { autoRecoveries = 0; epochMisses = 0; recoverRoom(auto = false); return }
        val stale = _state.value.descriptor
        val request = currentRequest ?: stale?.let { PlaybackRequest(it.id) } ?: return
        // A film resumes where it failed; live and archive streams restart where the server puts them.
        val resumeAt = if (stale != null && !stale.live) {
            positionMs ?: playbackPosition(stale)
        } else null
        attachJob?.cancel(); rewindJob?.cancel()
        _state.update { it.copy(failure = null, retrying = true, message = null) }
        attachJob = viewModelScope.launch {
            val owner = sessions.current().catalogScope()
            try {
                // Give back this player's own connection first: a one-stream source would answer source_busy.
                releaseCurrentBuffer()
                val fresh = coordinator.resolveAgain(if (request.preferHls) request.copy(startPositionMs = resumeAt) else request)
                currentCoroutineContext().ensureActive()
                if (sessions.current().catalogScope() != owner) return@launch
                start(fresh, resumeAt)
                if (fresh.live) loadChannelList(fresh)
                _state.update { it.copy(retrying = false) }
                showOsd()
            } catch (e: CancellationException) {
                _state.update { it.copy(retrying = false) }
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(retrying = false, failure = ApiErrors.describe(e), failureKeepsPlaying = false) }
            }
        }
    }

    /**
     * Puts this member back in step with the room's stream.
     *
     * First the descriptor as it is now (GET /rooms/{code}/descriptor): seat and socket are untouched,
     * and for a shared stream it carries the current `stream_epoch` and time zero. If the room no
     * longer honours the seat's token, or its socket is already over ([rejoin]), the room is joined
     * again with that token, which keeps the same seat while the room still has it. The media item is
     * then loaded again and RoomSync positions it.
     *
     * @param auto not asked for by the viewer: the badge says "Catching up with the room…"
     * @param closedReason the socket closed for this reason; if joining again fails, the room is over for it
     */
    private fun recoverRoom(auto: Boolean, rejoin: Boolean = false, closedReason: String? = null) {
        val held = seat ?: return
        // One at a time; a newer epoch that arrives meanwhile is picked up by the loop below and by keepInStep.
        // A closed socket is not put off: nothing else would bring the room back.
        if (_state.value.retrying && !rejoin) return
        attachJob?.cancel(); rewindJob?.cancel()
        val mine = ++recoverySeq
        _state.update { it.copy(failure = null, retrying = true, roomCatchUp = auto, message = null) }
        attachJob = viewModelScope.launch {
            try {
                reloadRoom(held, rejoin)
                _state.update { it.copy(retrying = false, roomCatchUp = false) }
                showOsd()
            } catch (e: CancellationException) {
                if (recoverySeq == mine) _state.update { it.copy(retrying = false, roomCatchUp = false) }
                throw e
            } catch (e: Exception) {
                if (recoverySeq != mine) return@launch
                _state.update { it.copy(retrying = false, roomCatchUp = false) }
                when {
                    closedReason != null -> roomOver(closedReason)
                    // The room went while this TV was away from it.
                    (e as? ApiException)?.error?.code == "room_not_found" -> roomOver("not_found")
                    else -> _state.update { it.copy(failure = ApiErrors.describe(e), failureKeepsPlaying = false) }
                }
            }
        }
    }

    /**
     * The work of [recoverRoom]: the member's descriptor as it is now (or, when the seat is gone or
     * [rejoin] is set, a join with the member token), loaded and positioned, chasing a room that was
     * seeked again meanwhile a few times. Failures are the caller's to word.
     */
    private suspend fun reloadRoom(held: RoomSeat, rejoin: Boolean) {
        releaseCurrentBuffer()
        var current = held
        var fresh = if (rejoin) null else try { rooms.descriptor(held) } catch (e: ApiException) { if (roomSeatLost(e.error?.code)) null else throw e }
        if (fresh == null) {
            // No close frame: the seat stays for the join to keep.
            roomClient?.keepSeatOnExit()
            roomJob?.let { it.cancel(); it.join() }
            current = rooms.rejoin(key, held)
            currentCoroutineContext().ensureActive()
            seat = current
            playingStream = current.descriptor.roomStream()
            startRoom(current, isHost = _state.value.room?.isHost == true)
            fresh = current.descriptor
        }
        var chases = 0
        while (true) {
            val d = checkNotNull(fresh)
            start(d)
            if (d.live) loadChannelList(d)
            // The room may have been seeked again while this one was loading.
            val behind = RoomSync.streamStep(playingStream, roomClient?.state?.value?.stream) == StreamStep.Reload
            epochMisses = if (behind) epochMisses + 1 else 0
            if (!behind || ++chases >= MAX_EPOCH_CHASES) break
            fresh = rooms.descriptor(current)
        }
    }

    /** Closes a failure that left the previous stream playing. */
    fun dismissFailure() { failedRequest = null; _state.update { it.copy(failure = null, failureKeepsPlaying = false) } }

    override fun onCleared() {
        rewindJob?.cancel(); attachJob?.cancel(); bufferHeartbeat?.cancel(); bufferSeekJob?.cancel()
        cleanupScope.launch { buffer.close() }
        // A room made from the menu that nobody was let into is closed rather than left to expire.
        (together.state.value?.phase as? WatchTogether.Phase.Ready)?.room?.code?.let { code ->
            cleanupScope.launch { try { rooms.close(code) } catch (_: Exception) { } }
        }
        coordinator.unregisterActivePlayer(key)
        // roomJob ends with viewModelScope; RoomClient.run() closes the socket normally on its way out, which frees the seat.
        rooms.release(key)
        // stop() reports the final position; release() alone would leave "continue watching" at the last 10 s tick.
        player.stop()
        player.release()
        coordinator.release(key)
    }

    companion object {
        const val OSD_HIDE_MS = 4_000L
        const val NUMBER_COMMIT_MS = ChannelNumberEntry.COMMIT_MS
        const val ZAP_SETTLE_MS = 350L
        /** Window for the second Back that swaps to the last channel; 500 ms was too tight for a TV remote. */
        const val BACK_DOUBLE_MS = 750L
        const val CONTROLS_HIDE_MS = 5_000L
        const val SCRUB_SETTLE_MS = 300L
        const val SCRUB_RELEASE_MS = 700L
        const val ROOM_SYNC_TICK_MS = 500L
        const val ROOM_SEEK_COOLDOWN_MS = 2_000L
        /** A live-room member this long well behind the others catches up (a burst alone does not last). */
        const val LIVE_LAG_SUSTAIN_MS = 2_000L
        /** The longest a live-room member holds for its reserve, as the browser's LIVE_HOLD_MAX_MS. */
        const val LIVE_HOLD_MAX_MS = 8_000L
        /** Recoveries of a room member's stream that are tried before the failure is put to the viewer. */
        const val MAX_AUTO_RECOVERIES = 2
        /** Descriptor fetches in one recovery while the room keeps being seeked under it. */
        const val MAX_EPOCH_CHASES = 3
        /** Descriptors in a row that were behind the room's stream before the viewer is asked. */
        const val MAX_EPOCH_MISSES = 6
        /** One automatic join after a lost seat or connection per this long: a seat that keeps being dropped ends the room for this TV. */
        const val AUTO_REJOIN_SPACING_MS = 30_000L
        /** The live reconnect attempt at which a room member's stream address is fetched again. */
        const val LIVE_ROOM_REFETCH_ATTEMPT = 3
        const val FLASH_MS = 2_500L
        /** The longest a room this TV started waits for the server to confirm where it was put. */
        const val CARRY_SETTLE_MS = 3_000L
        /** How often the skip / Up next state is looked at. */
        const val FLOW_TICK_MS = 250L
        /** How often feed failover looks at the picture. */
        const val FEED_SAMPLE_MS = 1_000L
        /** A step of the position larger than this in one look is a jump (a new stream), not play. */
        const val MAX_FEED_STEP_MS = 3_000L
        const val ROOM_HOST_ONLY = "Only the host can control playback"
        const val ROOM_RECONNECTING = "Reconnecting to the room…"
        val SLEEP_OPTIONS = listOf<Int?>(null, 15, 30, 60, 90, 120)
    }
}
