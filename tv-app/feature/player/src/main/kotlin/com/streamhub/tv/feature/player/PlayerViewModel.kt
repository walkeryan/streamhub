package com.streamhub.tv.feature.player

import android.os.SystemClock
import androidx.lifecycle.SavedStateHandle
import androidx.media3.common.Player
import com.streamhub.tv.core.data.repo.RoomRepository
import com.streamhub.tv.core.data.repo.RoomSeat
import com.streamhub.tv.core.data.room.RoomClient
import com.streamhub.tv.core.data.room.RoomClock
import com.streamhub.tv.core.data.room.RoomConnection
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
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import java.time.Instant
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.repo.ChannelListCache
import com.streamhub.tv.core.data.repo.PlaybackCoordinator
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
 *  - A refusal of the media URL itself (StreamRefusals: the session or account, the home network, the
 *    rating ceiling, the user's stream limit) is worded like the same refusal of a resolve. One about
 *    the session is first put to the server: refreshed and resolved again once, or, when the session
 *    really is over, left to the shell, which explains and returns to the picker or to pairing.
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
    playerFactory: PlayerFactory,
) : ViewModel() {
    private val key: String = checkNotNull(savedState["key"])
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
        viewModelScope.launch { player.state.map { it.isPlaying }.distinctUntilChanged().collect { if (it) reauthorized = false } }
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

    private suspend fun releaseCurrentBuffer() {
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
        _state.update { it.copy(descriptor=d, live=d.live, message=null, bufferError=null, bufferBusy=d.bufferAvailable) }
        var acquiredLeaseId: String? = null
        try {
            val lease=if(d.bufferAvailable) buffer.open(d) else null
            acquiredLeaseId=lease?.leaseId
            currentCoroutineContext().ensureActive()
            if(sessions.current().catalogScope()!=owner) throw CancellationException("Playback scope changed")
            currentStopped=false
            if (inRoom) {
                // One shared stream: its time zero is start_position_ms into the title, so the player starts
                // at the room's position minus that. A member's own stream covers the whole title.
                val stream = d.roomStream()
                playingStream = stream
                _state.update { it.copy(streamStartMs = stream?.startMs ?: 0, titleDurationMs = d.durationMs?.takeIf { len -> stream != null && len > 0 }) }
                // Until the socket has said where the room is, the descriptor's resume position is the room's.
                player.play(d, reportOwner, RoomSync.playerPosition(roomPosition() ?: d.resumePositionMs, stream))
                // A new room is paused at the start; until the room says "playing" this member waits.
                if (roomClient?.state?.value?.clock?.playing != true) player.pause()
            } else if (startPositionMs != null) player.play(d, reportOwner, startPositionMs) else player.play(d, reportOwner)
            _state.update { it.copy(bufferWindow=lease?.window,bufferBusy=false) }
            if(lease != null) bufferHeartbeat=viewModelScope.launch {
                try {
                    while(true) {
                        delay(30_000)
                        if(sessions.current().catalogScope()!=owner) throw CancellationException("Playback scope changed")
                        val window=buffer.heartbeat(paused=!player.exoPlayer.playWhenReady)
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
        } catch(_:Exception) {
            acquiredLeaseId?.let { id -> withContext(NonCancellable) { buffer.close(id) } }
            currentCoroutineContext().ensureActive()
            if(sessions.current().catalogScope()!=owner) return
            _state.update { it.copy(bufferBusy=false,bufferError="Buffer unavailable. Nothing was attached. Retry or choose live without buffering.",overlay=Overlay.Buffer) }
        }
    }

    fun playWithoutBuffer() {
        val descriptor=_state.value.descriptor ?: return
        attachJob?.cancel(); attachJob=viewModelScope.launch { start(descriptor.copy(bufferAvailable=false)); showOsd() }
    }

    fun openBuffer() { _state.update { it.copy(overlay=Overlay.Buffer) }; refreshBuffer() }
    fun refreshBuffer() {
        if(buffer.leaseId==null) return
        bufferSeekJob?.cancel()
        val lease=buffer.leaseId
        bufferSeekJob=viewModelScope.launch {
            val owner=sessions.current().catalogScope()
            try {
                val window=buffer.heartbeat(paused=!player.exoPlayer.playWhenReady)
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
        val idx = list.indexOfFirst { it.id == d.id }.takeIf { it >= 0 } ?: ctx?.index ?: -1
        _state.update { it.copy(channels = list, index = idx, osdChannel = list.getOrNull(idx)) }
    }

    // ---- OSD -----------------------------------------------------------------------------------

    /** Films, catch-up and buffered history are driven from the control row; only true live gets the banner. */
    private fun usesControls(s: PlayerScreenState = _state.value) = !s.live || s.bufferTimeshift

    fun showOsd() {
        if (_state.value.overlay in listOf(Overlay.MiniList, Overlay.Menu, Overlay.Rewind, Overlay.Buffer)) return
        if (usesControls()) { showControls(); return }
        _state.update { it.copy(overlay = Overlay.Osd) }
        osdJob?.cancel()
        osdJob = viewModelScope.launch {
            delay(OSD_HIDE_MS)
            if (_state.value.numberEntry.isEmpty()) _state.update { if (it.overlay == Overlay.Osd) it.copy(overlay = Overlay.None) else it }
        }
    }

    // ---- Film controls ------------------------------------------------------------------------

    fun showControls() {
        if (_state.value.overlay in listOf(Overlay.MiniList, Overlay.Menu, Overlay.Rewind, Overlay.Buffer)) return
        _state.update { it.copy(overlay = Overlay.Controls) }
        armControlsHide()
    }

    /** Any key press while the row is up counts as activity. */
    fun touchControls() { if (_state.value.overlay == Overlay.Controls) armControlsHide() }

    private fun armControlsHide() {
        osdJob?.cancel()
        osdJob = viewModelScope.launch {
            delay(CONTROLS_HIDE_MS)
            // A paused picture keeps its controls: they are the only sign of why it stopped.
            while (!player.exoPlayer.playWhenReady) delay(500)
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
        // In a room the bar starts from where the room is, not from where this player happens to be.
        val target = ((shown.scrubTargetMs ?: roomPosition() ?: shown.titlePositionMs(ps.positionMs)) + deltaMs).coerceIn(0, duration)
        _state.update { it.copy(scrubTargetMs = target) }
        scrubJob?.cancel()
        scrubJob = viewModelScope.launch {
            delay(SCRUB_SETTLE_MS)
            // The room's answer (a state message) moves every member, this one included.
            if (client != null) client.seek(target) else player.seekTo(target)
            // Keep showing the target until the player's position ticker has caught up with it.
            delay(SCRUB_RELEASE_MS)
            _state.update { it.copy(scrubTargetMs = null) }
        }
    }

    fun startOver() {
        scrubJob?.cancel()
        _state.update { it.copy(scrubTargetMs = null) }
        if (seat != null) roomControl { it.seek(0) } else player.seekTo(0)
    }

    // ---- Play / pause (the room's, when in one) -------------------------------------------------

    fun togglePlayPause() {
        if (seat == null) { player.togglePlayPause(); return }
        val playing = roomClient?.state?.value?.clock?.playing ?: player.exoPlayer.playWhenReady
        roomControl { if (playing) it.pause() else it.play() }
    }
    fun resumePlayback() { if (seat == null) player.resume() else roomControl { it.play() } }
    fun pausePlayback() { if (seat == null) player.pause() else roomControl { it.pause() } }

    // ---- Watch room ----------------------------------------------------------------------------

    val inRoom: Boolean get() = seat != null

    /** Where the room is now, when in a room that has a position (not a live one) and has said so. */
    private fun roomPosition(): Long? {
        val client = roomClient ?: return null
        return client.state.value.clock?.let { RoomSync.positionAt(it, client.serverNowMs()) }
    }

    private fun startRoom(s: RoomSeat) {
        val client = rooms.client(s)
        roomClient = client
        roomJob?.cancel()
        _state.update { it.copy(roomCode = s.code, room = client.state.value, roomEnded = null) }
        roomJob = viewModelScope.launch {
            // Cancelling this job is how the room is left: run() gives the seat back on its way out.
            launch { client.run() }
            launch {
                client.state.collect { room ->
                    _state.update { it.copy(room = room) }
                    when {
                        room.connection == RoomConnection.Closed -> roomClosed(room.closedReason)
                        // A seek restarted the room's stream: what is playing is a start that no longer exists.
                        RoomSync.streamStep(playingStream, room.stream) == StreamStep.Reload -> recoverRoom(auto = true)
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
        while (true) {
            delay(ROOM_SYNC_TICK_MS)
            val room = client.state.value
            val clock = room.clock ?: continue
            val s = _state.value
            if (room.connection != RoomConnection.Connected || currentStopped || s.failure != null || s.retrying) continue
            val exo = player.exoPlayer
            if (exo.playbackState == Player.STATE_IDLE || exo.playbackState == Player.STATE_ENDED || player.state.value.audioBlocked) continue
            val stream = playingStream
            val wanted = when (RoomSync.streamStep(stream, room.stream)) {
                // Positions on a start of the stream the room has left mean nothing: load the new one first.
                StreamStep.Reload -> { recoverRoom(auto = true); continue }
                // The descriptor is from a later start than the last state: play and pause still apply,
                // the position waits for the state that belongs to this stream.
                StreamStep.Wait -> clock.copy(positionMs = null)
                StreamStep.Same -> clock
            }
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

    /** A short message over the picture. */
    private fun flash(text: String) {
        if (text.isBlank()) return
        messageJob?.cancel()
        _state.update { it.copy(message = text) }
        messageJob = viewModelScope.launch { delay(FLASH_MS); _state.update { if (it.message == text) it.copy(message = null) else it } }
    }

    fun hideOverlay() { rewindJob?.cancel(); bufferSeekJob?.cancel(); _state.update { it.copy(overlay = Overlay.None, rewindLoading = false, bufferBusy=false, audioChoice=null) } }
    fun openMiniList() { if (seat == null && _state.value.live && _state.value.channels.isNotEmpty()) _state.update { it.copy(overlay = Overlay.MiniList) } }
    fun openMenu() { _state.update { it.copy(overlay = Overlay.Menu) } }

    fun openRewind() {
        if(seat != null) return
        if(buffer.leaseId != null) { openBuffer(); return }
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
                        currentRequest=result.request; failedRequest=null
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
    fun retry() {
        val zap = failedRequest
        if (zap != null) {
            failedRequest = null
            _state.update { it.copy(failure = null, message = null) }
            submitChannel(zap, archiveAction = zap.rewindAt != null)
            return
        }
        if (seat != null) { autoRecoveries = 0; recoverRoom(auto = false); return }
        val stale = _state.value.descriptor
        val request = currentRequest ?: stale?.let { PlaybackRequest(it.id) } ?: return
        // A film resumes where it failed; live and archive streams restart where the server puts them.
        val resumeAt = if (stale != null && !stale.live) player.exoPlayer.currentPosition.takeIf { it > 0 } else null
        attachJob?.cancel(); rewindJob?.cancel()
        _state.update { it.copy(failure = null, retrying = true, message = null) }
        attachJob = viewModelScope.launch {
            val owner = sessions.current().catalogScope()
            try {
                // Give back this player's own connection first: a one-stream source would answer source_busy.
                releaseCurrentBuffer()
                val fresh = coordinator.resolveAgain(request)
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
                    startRoom(current)
                    fresh = current.descriptor
                }
                var chases = 0
                while (true) {
                    val d = checkNotNull(fresh)
                    start(d)
                    if (d.live) loadChannelList(d)
                    // The room may have been seeked again while this one was loading.
                    if (RoomSync.streamStep(playingStream, roomClient?.state?.value?.stream) != StreamStep.Reload || ++chases >= MAX_EPOCH_CHASES) break
                    fresh = rooms.descriptor(current)
                }
                _state.update { it.copy(retrying = false, roomCatchUp = false) }
                showOsd()
            } catch (e: CancellationException) {
                if (recoverySeq == mine) _state.update { it.copy(retrying = false, roomCatchUp = false) }
                throw e
            } catch (e: Exception) {
                if (recoverySeq != mine) return@launch
                _state.update { it.copy(retrying = false, roomCatchUp = false) }
                if (closedReason != null) roomOver(closedReason)
                else _state.update { it.copy(failure = ApiErrors.describe(e), failureKeepsPlaying = false) }
            }
        }
    }

    /** Closes a failure that left the previous stream playing. */
    fun dismissFailure() { failedRequest = null; _state.update { it.copy(failure = null, failureKeepsPlaying = false) } }

    override fun onCleared() {
        rewindJob?.cancel(); attachJob?.cancel(); bufferHeartbeat?.cancel(); bufferSeekJob?.cancel()
        cleanupScope.launch { buffer.close() }
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
        /** Recoveries of a room member's stream that are tried before the failure is put to the viewer. */
        const val MAX_AUTO_RECOVERIES = 2
        /** Descriptor fetches in one recovery while the room keeps being seeked under it. */
        const val MAX_EPOCH_CHASES = 3
        /** One automatic join after a lost seat or connection per this long: a seat that keeps being dropped ends the room for this TV. */
        const val AUTO_REJOIN_SPACING_MS = 30_000L
        /** The live reconnect attempt at which a room member's stream address is fetched again. */
        const val LIVE_ROOM_REFETCH_ATTEMPT = 3
        const val FLASH_MS = 2_500L
        const val ROOM_HOST_ONLY = "Only the host can control playback"
        const val ROOM_RECONNECTING = "Reconnecting to the room…"
        val SLEEP_OPTIONS = listOf<Int?>(null, 15, 30, 60, 90, 120)
    }
}
