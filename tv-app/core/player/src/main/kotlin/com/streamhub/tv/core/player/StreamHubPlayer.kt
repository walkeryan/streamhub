package com.streamhub.tv.core.player

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy
import androidx.media3.extractor.DefaultExtractorsFactory
import androidx.media3.extractor.ts.DefaultTsPayloadReaderFactory
import androidx.media3.extractor.ts.TsExtractor
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackState
import com.streamhub.tv.core.data.model.ProgressReport
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.math.min

data class TrackOption(
    val label: String,
    val language: String?,
    val groupIndex: Int,
    val trackIndex: Int,
    val selected: Boolean,
    val revision: Long = 0,
)

data class PlayerUiState(
    val isPlaying: Boolean = false,
    val buffering: Boolean = false,
    val seekable: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val error: String? = null,
    val reconnectAttempt: Int = 0,
    val audioTracks: List<TrackOption> = emptyList(),
    val textTracks: List<TrackOption> = emptyList(),
    val subtitlesOff: Boolean = true,
    val audioBlocked: Boolean = false,
    val audioNotice: String? = null,
)

/**
 * Media3 wrapper used by the full-screen player and the Live TV preview.
 *
 *  - Per-descriptor headers (User-Agent/Referer from #EXTVLCOPT / Xtream) via DefaultHttpDataSource.Factory
 *  - Progressive MPEG-TS, HLS and DASH (DefaultMediaSourceFactory picks by MIME)
 *  - Live: reconnect with exponential backoff (1 s → 30 s, unlimited while screen is open)
 *  - Track selection helpers (audio / text / disable subtitles)
 *  - Progress reporting to POST /playback/progress every 10 s + on pause/stop (VOD only)
 */
@OptIn(UnstableApi::class)
class StreamHubPlayer(
    private val context: Context,
    private val api: StreamHubApi,
    private val reportProgress: Boolean = true,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var progressJob: Job? = null
    private var tickerJob: Job? = null
    private var reconnectJob: Job? = null
    private var current: PlaybackDescriptor? = null
    private var mediaEpoch=0L
    private var trackRevision=0L
    private var wantsPlayback=false
    private var waitingForAudio=false
    private var updatingMedia=false
    private val audioApproval=RuntimeAudioApproval<androidx.media3.common.TrackGroup>()

    private val _state = MutableStateFlow(PlayerUiState())
    val state: StateFlow<PlayerUiState> = _state.asStateFlow()

    private val httpFactory = DefaultHttpDataSource.Factory()
        .setAllowCrossProtocolRedirects(true)
        .setConnectTimeoutMs(10_000)
        .setReadTimeoutMs(15_000)
        .setUserAgent(DEFAULT_UA)

    private val extractors = DefaultExtractorsFactory()
        // Many IPTV streams start mid-GOP / without IDR; detect access units for H.264 without AUDs.
        .setTsExtractorFlags(
            DefaultTsPayloadReaderFactory.FLAG_ALLOW_NON_IDR_KEYFRAMES or DefaultTsPayloadReaderFactory.FLAG_DETECT_ACCESS_UNITS,
        )
        .setTsExtractorMode(TsExtractor.MODE_SINGLE_PMT)

    private val mediaSourceFactory = DefaultMediaSourceFactory(httpFactory, extractors)
        .setLoadErrorHandlingPolicy(DefaultLoadErrorHandlingPolicy(6))

    val exoPlayer: ExoPlayer = ExoPlayer.Builder(context)
        .setRenderersFactory(
            // EXTENSION_RENDERER_MODE_PREFER lets the FFmpeg audio extension (if bundled, see README)
            // take over AC-3/E-AC-3/DTS on devices lacking HW decoders. No-op without the extension.
            DefaultRenderersFactory(context).setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_PREFER),
        )
        .setMediaSourceFactory(mediaSourceFactory)
        .setLoadControl(
            DefaultLoadControl.Builder()
                .setBufferDurationsMs(15_000, 50_000, 1_500, 3_000)
                .build(),
        )
        .setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(), true)
        .setHandleAudioBecomingNoisy(true)
        .build()

    private val listener = object : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) {
            _state.value = _state.value.copy(isPlaying = isPlaying)
            if (!isPlaying && exoPlayer.playbackState == Player.STATE_READY) report(PlaybackState.PAUSED)
        }

        override fun onPlaybackStateChanged(playbackState: Int) {
            _state.value = _state.value.copy(buffering = playbackState == Player.STATE_BUFFERING)
            if (playbackState == Player.STATE_READY) {
                reconnectJob?.cancel()
                _state.value = _state.value.copy(error = null, reconnectAttempt = 0)
                enforceAudioLanguage()
            }
            if (playbackState == Player.STATE_ENDED) {
                if(current?.bufferAvailable == true) _state.value = _state.value.copy(error="Buffered playback ended. Choose Go Live or retry.")
                else if (current?.live == true) scheduleReconnect("Stream ended") else report(PlaybackState.STOPPED)
            }
        }

        override fun onPlayerError(error: PlaybackException) {
            val d = current
            if(d?.bufferAvailable == true) {
                _state.value = _state.value.copy(error="Buffered playback interrupted. Choose Go Live or retry.")
            } else if (d?.live == true) {
                scheduleReconnect(error.errorCodeName)
            } else if (error.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW) {
                exoPlayer.seekToDefaultPosition(); exoPlayer.prepare()
            } else {
                _state.value = _state.value.copy(error = friendly(error))
            }
        }

        override fun onTracksChanged(tracks: Tracks) {
            trackRevision++
            _state.value = _state.value.copy(
                audioTracks = options(tracks, C.TRACK_TYPE_AUDIO),
                textTracks = options(tracks, C.TRACK_TYPE_TEXT),
                subtitlesOff = exoPlayer.trackSelectionParameters.disabledTrackTypes.contains(C.TRACK_TYPE_TEXT) ||
                    options(tracks, C.TRACK_TYPE_TEXT).none { it.selected },
            )
            enforceAudioLanguage()
        }
    }

    init {
        exoPlayer.addListener(listener)
        // Subtitles off by default; user enables from the Menu overlay.
        exoPlayer.trackSelectionParameters = exoPlayer.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
            .build()
        tickerJob = scope.launch {
            while (isActive) {
                _state.value = _state.value.copy(
                    seekable = exoPlayer.isCurrentMediaItemSeekable && exoPlayer.duration > 0,
                    positionMs = exoPlayer.currentPosition.coerceAtLeast(0),
                    durationMs = exoPlayer.duration.takeIf { it != C.TIME_UNSET } ?: 0,
                )
                delay(500)
            }
        }
    }

    /** Start playing a descriptor (replaces whatever was playing). */
    fun play(d: PlaybackDescriptor, startPositionMs: Long = d.resumePositionMs) {
        if (current != null && current?.id != d.id) report(PlaybackState.STOPPED)
        updatingMedia=true
        exoPlayer.playWhenReady=false
        current = d
        mediaEpoch++;trackRevision++;audioApproval.clear()
        wantsPlayback=true;waitingForAudio=d.audioLanguageRequired
        exoPlayer.playWhenReady=false
        exoPlayer.trackSelectionParameters=exoPlayer.trackSelectionParameters.buildUpon()
            .clearOverridesOfType(C.TRACK_TYPE_AUDIO)
            .setPreferredAudioLanguages(*d.preferredAudioLanguages.toTypedArray()).build()
        reconnectJob?.cancel()
        _state.value = _state.value.copy(error = null, reconnectAttempt = 0,audioBlocked=false,audioNotice=null,audioTracks=emptyList())
        httpFactory.setDefaultRequestProperties(d.headers.filterKeys { !it.equals("User-Agent", true) })
        httpFactory.setUserAgent(d.headers.entries.firstOrNull { it.key.equals("User-Agent", true) }?.value ?: DEFAULT_UA)
        val item = MediaItemMapper.toMediaItem(d)
        if (d.live || startPositionMs <= 0) exoPlayer.setMediaItem(item) else exoPlayer.setMediaItem(item, startPositionMs)
        exoPlayer.prepare()
        updatingMedia=false
        exoPlayer.playWhenReady = !waitingForAudio
        enforceAudioLanguage()
        startProgressLoop()
    }

    val currentDescriptor: PlaybackDescriptor? get() = current

    private fun enforceAudioLanguage(): Boolean {
        if(updatingMedia) return false
        val d=current ?: return false
        val audio=options(exoPlayer.currentTracks,C.TRACK_TYPE_AUDIO)
        val explicit=exoPlayer.currentTracks.groups.any {group ->
            group.type==C.TRACK_TYPE_AUDIO && (0 until group.length).any {index->
                group.isTrackSelected(index) && audioApproval.accepts(mediaEpoch,group.mediaTrackGroup,index)
            }
        }
        val decision=runtimeAudioDecision(d.audioLanguageRequired,d.preferredAudioLanguages,audio.size,audio.filter{it.selected}.map{it.language},explicit)
        if(decision==RuntimeAudioDecision.Blocked || (decision==RuntimeAudioDecision.Unobservable && exoPlayer.playbackState!=Player.STATE_READY)) {
            exoPlayer.playWhenReady=false;waitingForAudio=true
            if(exoPlayer.playbackState==Player.STATE_READY) _state.value=_state.value.copy(audioBlocked=true,audioNotice="Preferred audio is unavailable or unverified. Choose Audio in playback controls.")
            return false
        }
        val wasBlocked=_state.value.audioBlocked
        _state.value=_state.value.copy(audioBlocked=false,audioNotice=if(decision==RuntimeAudioDecision.Unobservable) "Runtime audio tags unavailable; using your source selection. Language is not newly verified." else null,error=if(wasBlocked)null else _state.value.error)
        if(waitingForAudio) {waitingForAudio=false;exoPlayer.playWhenReady=wantsPlayback}
        return true
    }

    fun audioTrackNeedsConfirmation(option:TrackOption):Boolean = current?.let {
        it.audioLanguageRequired && !preferredAudioSelected(it.preferredAudioLanguages,listOf(option.language))
    } ?: true

    private fun scheduleReconnect(reason: String) {
        val d = current ?: return
        if (reconnectJob?.isActive == true) return
        reconnectJob = scope.launch {
            val attempt = _state.value.reconnectAttempt + 1
            val backoff = min(MAX_BACKOFF_MS, BASE_BACKOFF_MS shl min(attempt - 1, 5))
            _state.value = _state.value.copy(error = "Reconnecting… ($reason)", reconnectAttempt = attempt)
            delay(backoff)
            if (current === d) {
                updatingMedia=true
                mediaEpoch++;trackRevision++;audioApproval.clear();waitingForAudio=d.audioLanguageRequired
                exoPlayer.playWhenReady=false
                exoPlayer.trackSelectionParameters=exoPlayer.trackSelectionParameters.buildUpon().clearOverridesOfType(C.TRACK_TYPE_AUDIO).build()
                exoPlayer.setMediaItem(MediaItemMapper.toMediaItem(d))
                exoPlayer.prepare()
                updatingMedia=false
                exoPlayer.playWhenReady = wantsPlayback && !waitingForAudio
                enforceAudioLanguage()
            }
        }
    }

    private fun startProgressLoop() {
        progressJob?.cancel()
        if (!reportProgress) return
        progressJob = scope.launch {
            while (isActive) {
                delay(PROGRESS_INTERVAL_MS)
                if (exoPlayer.isPlaying) report(PlaybackState.PLAYING)
            }
        }
    }

    private fun report(state: PlaybackState) {
        val d = current ?: return
        if (!reportProgress || d.live) return
        val pos = exoPlayer.currentPosition
        val dur = exoPlayer.duration.takeIf { it != C.TIME_UNSET && it > 0 } ?: d.durationMs
        scope.launch {
            runCatching { api.reportProgress(ProgressReport(id = d.id, positionMs = pos, durationMs = dur, state = state)) }
        }
    }

    // ---- Track selection --------------------------------------------------------------------

    private fun options(tracks: Tracks, type: Int): List<TrackOption> {
        val out = mutableListOf<TrackOption>()
        tracks.groups.forEachIndexed { gi, g ->
            if (g.type != type) return@forEachIndexed
            for (ti in 0 until g.length) {
                if (!g.isTrackSupported(ti)) continue
                val f = g.getTrackFormat(ti)
                out += TrackOption(label(f, out.size), f.language, gi, ti, g.isTrackSelected(ti),trackRevision)
            }
        }
        return out
    }

    private fun label(f: Format, i: Int): String {
        val lang = f.language?.let { java.util.Locale.forLanguageTag(it).displayLanguage.ifBlank { it } }
        val ch = when (f.channelCount) { 1 -> "Mono"; 2 -> "Stereo"; 6 -> "5.1"; 8 -> "7.1"; else -> null }
        val codec = f.sampleMimeType?.substringAfter('/')?.uppercase()
        return listOfNotNull(f.label ?: lang ?: "Track ${i + 1}", ch, codec).joinToString(" · ")
    }

    fun selectTrack(option: TrackOption, type: Int, allowUnverifiedAudio:Boolean=false):Boolean {
        if(option.revision!=trackRevision) return false
        val group=exoPlayer.currentTracks.groups.getOrNull(option.groupIndex) ?: return false
        if(group.type!=type || option.trackIndex !in 0 until group.length || !group.isTrackSupported(option.trackIndex)) return false
        if(type==C.TRACK_TYPE_AUDIO) {
            if(audioTrackNeedsConfirmation(option) && !allowUnverifiedAudio) return false
            exoPlayer.playWhenReady=false;waitingForAudio=true
            audioApproval.approve(mediaEpoch,group.mediaTrackGroup,option.trackIndex)
        }
        exoPlayer.trackSelectionParameters=exoPlayer.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(type,false)
            .setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup,option.trackIndex)).build()
        if(type==C.TRACK_TYPE_AUDIO) enforceAudioLanguage()
        return true
    }

    fun disableSubtitles() {
        exoPlayer.trackSelectionParameters = exoPlayer.trackSelectionParameters.buildUpon()
            .clearOverridesOfType(C.TRACK_TYPE_TEXT)
            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
            .build()
        _state.value = _state.value.copy(subtitlesOff = true)
    }

    fun setPreferredAudioLanguages(langs: List<String>) {
        if (langs.isEmpty()) return
        exoPlayer.trackSelectionParameters = exoPlayer.trackSelectionParameters.buildUpon()
            .setPreferredAudioLanguages(*langs.toTypedArray())
            .build()
    }

    fun pause() {wantsPlayback=false;exoPlayer.pause()}
    fun resume() {wantsPlayback=true;if(enforceAudioLanguage())exoPlayer.play()}
    fun togglePlayPause() {if(exoPlayer.isPlaying)pause() else resume()}
    fun seekBy(ms: Long) {
        if (exoPlayer.isCurrentMediaItemSeekable && exoPlayer.duration > 0) {
            exoPlayer.seekTo((exoPlayer.currentPosition + ms).coerceIn(0, exoPlayer.duration))
        }
    }

    fun stop() {
        report(PlaybackState.STOPPED)
        current=null;wantsPlayback=false;waitingForAudio=false;audioApproval.clear();mediaEpoch++;trackRevision++
        exoPlayer.stop()
        _state.value=_state.value.copy(audioBlocked=false,audioNotice=null,audioTracks=emptyList())
        progressJob?.cancel()
        reconnectJob?.cancel()
    }

    fun release() {
        report(PlaybackState.STOPPED)
        exoPlayer.removeListener(listener)
        exoPlayer.release()
        scope.cancel()
    }

    private fun friendly(e: PlaybackException): String = when (e.errorCode) {
        PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS -> "The source refused the stream (HTTP error)."
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT -> "Can't reach the stream. Check your network."
        PlaybackException.ERROR_CODE_DECODER_INIT_FAILED,
        PlaybackException.ERROR_CODE_DECODING_FORMAT_UNSUPPORTED -> "This TV can't decode the stream. Try proxy/transcode mode on the server."
        else -> "Playback error: ${e.errorCodeName}"
    }

    companion object {
        const val PROGRESS_INTERVAL_MS = 10_000L
        const val BASE_BACKOFF_MS = 1_000L
        const val MAX_BACKOFF_MS = 30_000L
        const val DEFAULT_UA = "StreamHub-AndroidTV/1.0 (Media3)"
    }
}
