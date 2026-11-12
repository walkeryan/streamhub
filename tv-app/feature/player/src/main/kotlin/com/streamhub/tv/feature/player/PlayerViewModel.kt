package com.streamhub.tv.feature.player

import androidx.lifecycle.SavedStateHandle
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
)

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
 */
@HiltViewModel
class PlayerViewModel @Inject constructor(
    savedState: SavedStateHandle,
    private val coordinator: PlaybackCoordinator,
    private val channelCache: ChannelListCache,
    private val launcher: DeepLinkLauncher,
    private val api: StreamHubApi,
    private val sessions: SessionStore,
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
            attachJob = viewModelScope.launch { start(d); if(d.live) loadChannelList(d) }
        }
        showOsd()
        viewModelScope.launch {
            sessions.session.map { it.catalogScope() }.distinctUntilChanged().drop(1).collect {
                rewindJob?.cancel(); _state.update { it.copy(audioChoice=null) }; stopCurrentPlayback(); _exit.emit(Unit)
            }
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

    private suspend fun start(d: PlaybackDescriptor, startPositionMs: Long? = null) {
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
            if (startPositionMs != null) player.play(d, reportOwner, startPositionMs) else player.play(d, reportOwner)
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
        if (!ps.seekable || ps.durationMs <= 0) return
        val target = ((_state.value.scrubTargetMs ?: ps.positionMs) + deltaMs).coerceIn(0, ps.durationMs)
        _state.update { it.copy(scrubTargetMs = target) }
        scrubJob?.cancel()
        scrubJob = viewModelScope.launch {
            delay(SCRUB_SETTLE_MS)
            player.seekTo(target)
            // Keep showing the target until the player's position ticker has caught up with it.
            delay(SCRUB_RELEASE_MS)
            _state.update { it.copy(scrubTargetMs = null) }
        }
    }

    fun startOver() {
        scrubJob?.cancel()
        _state.update { it.copy(scrubTargetMs = null) }
        player.seekTo(0)
    }

    fun hideOverlay() { rewindJob?.cancel(); bufferSeekJob?.cancel(); _state.update { it.copy(overlay = Overlay.None, rewindLoading = false, bufferBusy=false, audioChoice=null) } }
    fun openMiniList() { if (_state.value.live && _state.value.channels.isNotEmpty()) _state.update { it.copy(overlay = Overlay.MiniList) } }
    fun openMenu() { _state.update { it.copy(overlay = Overlay.Menu) } }

    fun openRewind() {
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
        if (!s.live || s.channels.isEmpty()) return
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
        if (!_state.value.live) return
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

    /** Closes a failure that left the previous stream playing. */
    fun dismissFailure() { failedRequest = null; _state.update { it.copy(failure = null, failureKeepsPlaying = false) } }

    override fun onCleared() {
        rewindJob?.cancel(); attachJob?.cancel(); bufferHeartbeat?.cancel(); bufferSeekJob?.cancel()
        cleanupScope.launch { buffer.close() }
        coordinator.unregisterActivePlayer(key)
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
        val SLEEP_OPTIONS = listOf<Int?>(null, 15, 30, 60, 90, 120)
    }
}
