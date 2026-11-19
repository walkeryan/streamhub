package com.streamhub.tv.feature.player

import com.streamhub.tv.core.ui.theme.glass
import com.streamhub.tv.core.ui.components.PictureGlass
import com.streamhub.tv.core.ui.components.GlassIcons
import com.streamhub.tv.core.ui.components.QrTile
import com.streamhub.tv.core.ui.components.BigCode
import com.streamhub.tv.core.ui.components.GroupLabel
import com.streamhub.tv.core.ui.components.DialogText
import com.streamhub.tv.core.ui.components.DialogTitle
import com.streamhub.tv.core.ui.components.GlassPanel
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.border
import androidx.compose.foundation.Image
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.foundation.background
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.focusable
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.animation.core.animateDpAsState
import com.streamhub.tv.core.ui.components.ChoiceListDialog
import com.streamhub.tv.core.ui.components.ListChoice
import com.streamhub.tv.core.player.TrackOption
import androidx.compose.runtime.Composable
import androidx.compose.runtime.produceState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.nativeKeyCode
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.C
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.ui.components.AudioSourceDialog
import com.streamhub.tv.core.ui.components.ErrorDialog
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.data.room.RoomCode
import com.streamhub.tv.core.data.room.RoomConnection
import com.streamhub.tv.core.data.room.RoomUiState
import com.streamhub.tv.core.player.PlayerUiState
import com.streamhub.tv.core.player.VideoSurface
import com.streamhub.tv.core.ui.components.ProgressLine
import com.streamhub.tv.core.ui.components.QrCode
import com.streamhub.tv.core.ui.components.WatchTogetherDialog
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.sp
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.components.programmeProgress
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.input.ChannelNumberEntry
import com.streamhub.tv.core.ui.input.ChannelNumberOverlay
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

private val HHMM = DateTimeFormatter.ofPattern("HH:mm")
private fun hhmm(iso: String) = runCatching { ZonedDateTime.parse(iso).withZoneSameInstant(ZoneId.systemDefault()).format(HHMM) }.getOrDefault("")
private fun fmt(ms: Long): String {
    val s = ms / 1000
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, (s / 60) % 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
}

/** Hold time after which OK counts as a long press (Android's default long-press timeout). */
private const val LONG_PRESS_MS = 500L

/** Appended to audio and subtitle tracks that are in the file but have no decoder on this device. */
internal const val UNSUPPORTED_TRACK = " (not supported on this TV)"

/** Tracks one OK press across its KeyDown / auto-repeat / KeyUp events (not state: no recomposition). */
private class OkPress {
    var down = false
    var longHandled = false
}

/**
 * Remote mapping (decided by [PlayerKeyMap], which is unit-tested)
 *  Live:  Up/Down, CH+/CH- = zap · 0-9 = channel number (commits after 2 s or OK) · OK = mini channel list
 *         Left = archive · Right = menu · Back = exit; Back twice within 750 ms = last channel
 *  Film:  OK or Down = control row (scrubber, Play/Pause, −10, +10, Start over, Audio & subtitles), which
 *         hides after 5 s without a key press · Left/Right = seek 10 s, faster while held
 *  Hold OK (or Menu) opens the audio/subtitle/picture menu. Google TV remotes have no Menu key, so the
 *         short-press OK action fires on key *up* and a hold opens the menu.
 *  Room:  play, pause and seek are the room's (everyone moves together); a live room does not zap and
 *         OK plays/pauses; Back asks "Leave the room?" before leaving
 *  Skip:  while the controls are hidden OK activates the visible Skip intro / Skip credits (or the Up next
 *         card's Play next); Back dismisses the Up next card ("Keep watching")
 *  Mini:  the minimise button shrinks the player into picture-in-picture when [onMinimise] is given;
 *         Back does too while a title plays, but only with the profile's "Back shrinks to mini player"
 *         (`back_minimises`, off by default: Back closes the player). [inPip] shows the picture alone
 */
@Composable
fun PlayerScreen(onExit: () -> Unit, vm: PlayerViewModel = hiltViewModel(), onMinimise: (() -> Unit)? = null, inPip: Boolean = false) {
    val s by vm.state.collectAsStateWithLifecycle()
    val ps by vm.player.state.collectAsStateWithLifecycle()
    val together by vm.watchTogether.collectAsStateWithLifecycle()
    if (inPip) {
        // Picture-in-picture: the picture alone; the web app's bar carries the controls.
        Box(Modifier.fillMaxSize().background(Color.Black)) { VideoSurface(vm.player, Modifier.fillMaxSize(), s.aspect) }
        return
    }
    val rootFocus = remember { FocusRequester() }
    val timelineFocus = remember { FocusRequester() }
    val skipFocus = remember { FocusRequester() }
    val cardFocus = remember { FocusRequester() }
    val scope = rememberCoroutineScope()
    var lastBackAt by remember { mutableLongStateOf(0L) }
    var pendingExit by remember { mutableStateOf<Job?>(null) }
    var backHint by remember { mutableStateOf(false) }
    var backHintShown by remember { mutableStateOf(false) }
    val ok = remember { OkPress() }
    val room = s.room
    val inRoom = s.roomCode != null

    LaunchedEffect(Unit) { vm.exit.collect { onExit() } }
    LaunchedEffect(ps.audioBlocked) {if(ps.audioBlocked) vm.openMenu()}
    // Focus comes home when an overlay closes, and when a skip button or the card that held it goes away.
    LaunchedEffect(s.overlay, s.skipIntroTo != null, s.skipCredits, s.upNextCard != null, s.stillWatching, s.picker) {
        if ((s.overlay == Overlay.None || s.overlay == Overlay.Osd) && !s.stillWatching && s.picker == null) runCatching { rootFocus.requestFocus() }
    }

    fun perform(command: PlayerCommand) {
        when (command) {
            PlayerCommand.None, PlayerCommand.Back -> Unit
            is PlayerCommand.Zap -> vm.zap(command.delta)
            is PlayerCommand.Digit -> vm.digit(command.value)
            PlayerCommand.OpenChannelList -> vm.openMiniList()
            PlayerCommand.OpenMenu -> vm.openMenu()
            PlayerCommand.OpenArchive -> vm.openRewind()
            PlayerCommand.ShowControls, PlayerCommand.ShowInfo -> vm.showOsd()
            // Through the view model: in a watch room these go to the room, not to this player.
            PlayerCommand.TogglePlayPause -> { vm.togglePlayPause(); vm.showOsd() }
            PlayerCommand.Play -> { vm.resumePlayback(); vm.showOsd() }
            PlayerCommand.Pause -> { vm.pausePlayback(); vm.showOsd() }
            // Seeking from the remote puts focus on the timeline, so the next Left/Right keeps seeking.
            is PlayerCommand.Seek -> { vm.scrubBy(command.deltaMs); vm.showOsd(ControlsFocus.Timeline) }
            PlayerCommand.LastChannel -> vm.lastChannel()
            PlayerCommand.NextItem -> vm.playNextEpisode()
            PlayerCommand.PreviousItem -> vm.playPreviousEpisode()
        }
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black)
            .focusRequester(rootFocus)
            .onPreviewKeyEvent { e ->
                val code = e.key.nativeKeyCode
                val menuOpen = s.overlay == Overlay.MiniList || s.overlay == Overlay.Menu || s.overlay == Overlay.Rewind || s.overlay == Overlay.Buffer
                val controls = s.overlay == Overlay.Controls
                val live = s.live && !s.bufferTimeshift
                // Every press counts as activity for the control row's 5 s idle timer, and shows somebody is
                // watching (Still watching? counts autoplays with no press between).
                if (e.type == KeyEventType.KeyDown) { vm.touchControls(); vm.userInteracted() }
                fun resolve(longPress: Boolean = false) = PlayerKeyMap.resolve(code, live, menuOpen, controls, longPress, inRoom)
                if (resolve() == PlayerCommand.Back) {
                    if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent true
                    val action = PlayerKeyMap.back(BackContext(
                        upNextCard = s.upNextCard != null,
                        menuOpen = menuOpen,
                        controlsVisible = controls,
                        inRoom = inRoom,
                        typingNumber = s.numberEntry.isNotEmpty(),
                        liveWithLastChannel = s.live && vm.hasLastChannel,
                        canMinimise = onMinimise != null,
                        titlePlaying = !s.live && s.descriptor != null && vm.player.wantsToPlay,
                        backMinimises = s.backMinimises,
                    ))
                    when (action) {
                        BackAction.DismissUpNext -> { vm.dismissUpNext(); if (controls) vm.hideOverlay() }
                        BackAction.HideOverlay -> vm.hideOverlay()
                        BackAction.AskLeaveRoom -> vm.askLeaveRoom()
                        BackAction.CommitNumber -> vm.commitNumber()
                        BackAction.LastChannelWindow -> {
                            val now = System.currentTimeMillis()
                            if (now - lastBackAt < PlayerViewModel.BACK_DOUBLE_MS) {
                                pendingExit?.cancel(); lastBackAt = 0; backHint = false; vm.lastChannel()
                            } else {
                                lastBackAt = now
                                // Taught once per visit: a second Back inside the window swaps channels instead of leaving.
                                if (!backHintShown) { backHintShown = true; backHint = true }
                                pendingExit = scope.launch { delay(PlayerViewModel.BACK_DOUBLE_MS); backHint = false; onExit() }
                            }
                        }
                        // Only with "Back shrinks to mini player" on: the web app shows underneath.
                        BackAction.Minimise -> onMinimise?.invoke()
                        BackAction.Exit -> onExit()
                    }
                    return@onPreviewKeyEvent true
                }
                // OK / D-pad centre: short press acts on key up; holding it opens the menu. Every edge of a
                // press that started here is consumed, including the KeyUp after the menu has opened, so the
                // release can't click the menu item that just took focus. While a menu or the control row
                // holds focus a new press is theirs.
                if (PlayerKeyMap.isSelect(code) && (ok.down || (!menuOpen && !controls && e.type == KeyEventType.KeyDown))) {
                    val n = e.nativeKeyEvent
                    when (e.type) {
                        KeyEventType.KeyDown -> when {
                            !ok.down -> { ok.down = true; ok.longHandled = false }
                            !ok.longHandled && (n.isLongPress || n.eventTime - n.downTime >= LONG_PRESS_MS) -> {
                                ok.longHandled = true; perform(resolve(longPress = true))
                            }
                        }
                        KeyEventType.KeyUp -> {
                            val wasLong = ok.longHandled || n.eventTime - n.downTime >= LONG_PRESS_MS
                            val handled = ok.longHandled
                            ok.down = false; ok.longHandled = false
                            when {
                                handled -> Unit
                                // remotes that send no auto-repeat while held
                                wasLong -> perform(resolve(longPress = true))
                                vm.commitNumber() -> Unit
                                // Netflix-style: OK takes the skip button (or the card) that is showing.
                                !s.live && vm.activateVisibleSkip() -> Unit
                                else -> perform(resolve())
                            }
                        }
                    }
                    return@onPreviewKeyEvent true
                }
                if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                // None = the overlay's or control row's focusables handle the key.
                val command = resolve()
                if (command == PlayerCommand.None) false else { perform(command); true }
            }
            .focusable(),
    ) {
        VideoSurface(vm.player, Modifier.fillMaxSize(), s.aspect)
        if(s.overlay == Overlay.Rewind && s.audioChoice == null) ProviderRewindDialog(vm, s)
        if(s.overlay == Overlay.Buffer && s.audioChoice == null) BufferPlaybackDialog(vm,s,ps)
        s.audioChoice?.let { choice -> AudioSourceDialog(choice.options, vm::cancelAudioChoice, onChoose=vm::confirmAudioChoice) }

        // A stopped stream asks for a decision instead of leaving a badge over a black screen. Live
        // reconnect attempts (reconnectAttempt > 0) are still in progress and stay a badge.
        val failure = s.failure ?: ps.error?.takeIf { ps.reconnectAttempt == 0 && !s.retrying && !ps.audioBlocked }
            ?.let { UserError("Playback stopped", it, canRetry = true) }
        val status = when {
            s.retrying -> if (s.roomCatchUp) "Catching up with the room…" else "Trying again…"
            failure != null -> null
            ps.error != null -> ps.error
            s.message != null -> s.message
            room?.connection == RoomConnection.Reconnecting -> "Reconnecting to the room…"
            s.bufferBusy -> "Preparing buffer…"
            ps.buffering -> "Buffering…"
            else -> null
        }
        if (status != null) StatusBadge(status, Modifier.align(Alignment.Center))
        if (backHint) StatusBadge("Back again for last channel", Modifier.align(Alignment.TopCenter).padding(top = Tokens.Canvas.SafeMarginY))
        // Watch room: who is here (with the controls, or whenever the room is waiting), and why it paused.
        if (inRoom && room != null && s.roomEnded == null) {
            if (s.overlay != Overlay.None || room.waitingFor != null || room.connection != RoomConnection.Connected) {
                RoomStrip(s.roomCode.orEmpty(), room, Modifier.align(Alignment.TopStart).padding(start = Tokens.Canvas.SafeMarginX, top = Tokens.Canvas.SafeMarginY))
            }
            room.waitingFor?.let { StatusBadge("Waiting for $it…", Modifier.align(Alignment.TopCenter).padding(top = Tokens.Canvas.SafeMarginY + 44.dp)) }
        }
        // "Watch together" from the menu: the new room's code and QR code; Start moves this playback into it.
        together?.let { WatchTogetherDialog(it, onStart = vm::startTogetherRoom, onRetry = vm::retryTogetherRoom, onCancel = vm::cancelTogetherRoom, carryOn = true) }
        if (s.stillWatching) {
            Dialog(onDismissRequest = vm::stillWatchingBack, properties = DialogProperties(usePlatformDefaultWidth = false)) {
                StillWatchingPanel(s.upNext.next, onContinue = vm::stillWatchingContinue, onBack = vm::stillWatchingBack)
            }
        }
        s.picker?.let { TrackPickerDialog(vm, it, ps) }
        if (s.leaveAsk) LeaveRoomDialog(isHost = room?.isHost == true, onLeave = vm::leaveRoom, onEnd = vm::endRoom, onStay = vm::stayInRoom)
        // The room is over: say why, then back to the title it was opened from.
        s.roomEnded?.let { ErrorDialog(it, onRetry = onExit, onDismiss = onExit, dismissLabel = "Back") }
        if (failure != null && s.audioChoice == null && s.roomEnded == null) {
            val keepsPlaying = s.failure != null && s.failureKeepsPlaying
            ErrorDialog(
                failure,
                onRetry = vm::retry,
                // Back leaves the player unless the previous channel is still on screen behind the dialog.
                onDismiss = { if (keepsPlaying) vm.dismissFailure() else onExit() },
                dismissLabel = if (keepsPlaying) "Keep watching" else "Back",
            )
        }

        AnimatedVisibility(s.overlay == Overlay.Osd || s.numberEntry.isNotEmpty(), enter = fadeIn(), exit = fadeOut(), modifier = Modifier.align(Alignment.BottomCenter)) {
            Osd(s, ps)
        }
        val controlsUp = s.overlay == Overlay.Controls && s.numberEntry.isEmpty()
        AnimatedVisibility(controlsUp, enter = fadeIn(), exit = fadeOut(), modifier = Modifier.fillMaxSize()) {
            val links = ControlsLinks(above = when { s.skipIntroTo != null || s.skipCredits -> skipFocus; s.upNextCard != null -> cardFocus; else -> null })
            VodControls(controlsModel(s, ps, vm), controlsActions(vm, s, onExit, onMinimise), s.controlsFocus, links, timelineFocus)
        }
        SkipAndUpNext(vm, s, controlsUp, skipFocus, cardFocus, timelineFocus, Modifier.align(Alignment.BottomEnd))
        if (s.numberEntry.isNotEmpty()) {
            ChannelNumberOverlay(
                s.numberEntry, ChannelNumberEntry.match(s.numberEntry, s.channels) { it.number }?.name,
                Modifier.align(Alignment.TopEnd).padding(top = Tokens.Canvas.SafeMarginY, end = Tokens.Canvas.SafeMarginX),
            )
        }

        AnimatedVisibility(
            s.overlay == Overlay.MiniList,
            enter = slideInHorizontally { -it } + fadeIn(),
            exit = slideOutHorizontally { -it } + fadeOut(),
            modifier = Modifier.align(Alignment.CenterStart),
        ) {
            MiniList(s.channels, s.descriptor?.id) { ch -> vm.hideOverlay(); vm.tuneTo(ch) }
        }
        AnimatedVisibility(
            s.overlay == Overlay.Menu,
            enter = slideInHorizontally { it } + fadeIn(),
            exit = slideOutHorizontally { it } + fadeOut(),
            modifier = Modifier.align(Alignment.CenterEnd),
        ) {
            PlayerMenu(vm, s, ps, canMinimise = onMinimise != null)
        }
    }
}

@Composable
private fun StatusBadge(text: String, modifier: Modifier) {
    val g = glass
    Text(
        text, color = g.text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold,
        modifier = modifier.shadow(12.dp, RoundedCornerShape(14.dp)).clip(RoundedCornerShape(14.dp)).background(g.panel)
            .border(1.dp, g.line, RoundedCornerShape(14.dp)).padding(horizontal = 22.dp, vertical = 13.dp),
    )
}

/** Bottom OSD: live = number, logo, name, now/next + progress; VOD = title + scrubber. */
@Composable
private fun Osd(s: PlayerScreenState, ps: PlayerUiState) {
    val t = LocalThemeTokens.current
    val d = s.descriptor
    Column(
        Modifier.fillMaxWidth()
            .background(Brush.verticalGradient(0f to Color.Transparent, 1f to Color.Black.copy(alpha = 0.92f)))
            .padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = Tokens.Canvas.SafeMarginY)
            .padding(top = 64.dp),
    ) {
        if (s.live && !s.bufferTimeshift) {
            val ch = s.osdChannel
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (ch?.logo != null) AsyncImage(ch.logo, null, contentScale = ContentScale.Fit, modifier = Modifier.size(64.dp))
                Column(Modifier.padding(start = 16.dp)) {
                    Text(listOfNotNull(ch?.number?.toString(), ch?.name ?: d?.title).joinToString("  "), style = MaterialTheme.typography.headlineSmall, color = Color.White)
                    ch?.now?.let { Text("Now  ${hhmm(it.start)}–${hhmm(it.stop)}  ${it.title}", style = MaterialTheme.typography.bodyLarge, color = Color.White, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                    ch?.next?.let { Text("Next  ${hhmm(it.start)}  ${it.title}", style = MaterialTheme.typography.bodyMedium, color = Color.White.copy(alpha = 0.75f), maxLines = 1) }
                }
            }
            programmeProgress(s.osdChannel?.now)?.let { ProgressLine(it, Modifier.padding(top = 12.dp)) }
            if (s.osdChannel != null && s.osdChannel.id != d?.id) {
                Text("Switching…", color = t.accent, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 6.dp))
            }
        } else {
            Text(d?.title.orEmpty(), style = MaterialTheme.typography.headlineSmall, color = Color.White)
            d?.subtitle?.let { Text(it, style = MaterialTheme.typography.bodyLarge, color = Color.White.copy(alpha = 0.8f)) }
            // The title's position and length: a room's shared stream starts part-way in.
            val position = s.titlePositionMs(ps.positionMs)
            val duration = s.titleDurationMs(ps.durationMs)
            if (duration > 0) {
                ProgressLine((position.toFloat() / duration).coerceIn(0f, 1f), Modifier.padding(top = 12.dp))
                Row(Modifier.fillMaxWidth().padding(top = 4.dp)) {
                    Text(fmt(position), color = Color.White, style = MaterialTheme.typography.labelLarge)
                    Spacer(Modifier.weight(1f))
                    Text(if (ps.isPlaying) "▶" else "❚❚", color = Color.White, style = MaterialTheme.typography.labelLarge)
                    Spacer(Modifier.weight(1f))
                    Text("-" + fmt((duration - position).coerceAtLeast(0)), color = Color.White, style = MaterialTheme.typography.labelLarge)
                }
            }
        }
        Text(
            when {
                s.roomCode != null && s.live -> "OK play/pause for everyone · ▶ menu · Back to leave the room"
                s.live && !s.bufferTimeshift -> "▲▼ zap · OK list · ◀ archive · ▶ menu · Back×2 last channel"
                else -> "OK or ▼ for playback controls"
            },
            style = MaterialTheme.typography.labelMedium, color = Color.White.copy(alpha = 0.7f), modifier = Modifier.padding(top = 10.dp),
        )
        ps.audioNotice?.let {Text(it,color=t.textMuted,style=MaterialTheme.typography.labelMedium)}
        s.sleepEndsAt?.let {
            val min = ((it - System.currentTimeMillis()) / 60_000).coerceAtLeast(0)
            Text("Sleep in $min min", style = MaterialTheme.typography.labelMedium, color = t.accent)
        }
    }
}

/** The control overlay's view of the screen (titles, timeline, labels, the lines worth saying). */
private fun controlsModel(s: PlayerScreenState, ps: PlayerUiState, vm: PlayerViewModel): ControlsModel {
    val d = s.descriptor
    val (kicker, title) = d?.let(::titleBlock) ?: (null to "")
    val room = s.room
    val position = s.scrubTargetMs ?: s.titlePositionMs(ps.positionMs)
    val duration = s.titleDurationMs(ps.durationMs)
    val seekable = s.canSeek(ps.seekable)
    val plan = s.upNext
    val seeking = s.scrubTargetMs != null
    val notes = listOfNotNull(
        when {
            room != null && room.controlsLocked && !room.isHost -> "Only the host can control playback"
            room != null -> "Play, pause and seek move everyone in the room"
            s.bufferTimeshift -> "Buffered history · Go Live is in Settings"
            d?.archiveStart != null -> "Provider archive · Go Live is in Settings"
            !seekable -> "This stream can't be seeked"
            else -> null
        },
        ps.audioNotice,
        s.sleepEndsAt?.let { "Sleep in ${((it - System.currentTimeMillis()) / 60_000).coerceAtLeast(0)} min" },
    )
    val audio = ps.audioTracks.firstOrNull { it.selected }?.label?.let(::shortTrackLabel) ?: "Audio"
    val subtitles = if (ps.subtitlesOff) "Off" else ps.textTracks.firstOrNull { it.selected }?.label?.let(::shortTrackLabel) ?: "Off"
    return ControlsModel(
        kicker = kicker,
        title = title,
        positionMs = position,
        bufferedMs = s.titlePositionMs(ps.bufferedMs),
        durationMs = duration,
        seeking = seeking,
        thumbnailUrl = if (seeking) thumbnailUrl(d?.thumbnails, position, vm.serverUrl) else null,
        markers = d?.markers.orEmpty(),
        // In a room the button says what the room is doing: this player follows a moment later.
        playing = room?.clock?.playing ?: ps.isPlaying,
        seekable = seekable,
        episodeButtons = plan.kind != UpNextFlow.Kind.None && (plan.next != null || plan.previous != null),
        hasPrevious = plan.previous != null,
        hasNext = plan.next != null,
        audioLabel = audio,
        subtitleLabel = subtitles,
        notes = notes,
    )
}

private fun controlsActions(vm: PlayerViewModel, s: PlayerScreenState, onExit: () -> Unit, onMinimise: (() -> Unit)?) = ControlsActions(
    onClose = onExit,
    onScrub = vm::scrubBy,
    onTogglePlay = vm::togglePlayPause,
    onBack10 = { vm.scrubBy(-PlayerKeyMap.SEEK_STEP_MS) },
    onForward30 = { vm.scrubBy(PlayerKeyMap.MEDIA_SEEK_STEP_MS) },
    onPrevious = vm::playPreviousEpisode,
    onNext = vm::playNextEpisode,
    onAudio = { vm.openPicker(TrackPicker.Audio) },
    onSubtitles = { vm.openPicker(TrackPicker.Subtitles) },
    // A room for whatever is playing, with this TV as its host, where the web player offers one.
    onWatchTogether = if (vm.roomStart(s) != null) vm::startWatchTogether else null,
    // A room is other people: it is left on purpose, never shrunk away.
    onMinimise = onMinimise?.takeIf { s.roomCode == null },
    onSettings = vm::openMenu,
)

/**
 * Skip intro / Skip credits and the Up next card, bottom-right. They sit above the timeline while
 * the controls are up (and can be focused from it with Up) and drop to the bottom when they are not,
 * where OK takes them.
 */
@Composable
private fun SkipAndUpNext(vm: PlayerViewModel, s: PlayerScreenState, controlsUp: Boolean, skipFocus: FocusRequester, cardFocus: FocusRequester, timelineFocus: FocusRequester, modifier: Modifier) {
    val skipLabel = when {
        s.skipIntroTo != null -> "Skip intro"
        s.skipCredits -> "Skip credits"
        else -> null
    }
    val card = s.upNextCard
    if (skipLabel == null && card == null) return
    val bottom by animateDpAsState(if (controlsUp) 185.dp else 33.dp, label = "skipLift")
    Column(
        modifier.padding(end = Tokens.Canvas.SafeMarginX, bottom = bottom),
        horizontalAlignment = Alignment.End,
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        AnimatedVisibility(card != null, enter = fadeIn() + slideInHorizontally { it / 3 }, exit = fadeOut()) {
            card?.let {
                UpNextCard(
                    it, onPlay = vm::playNextEpisode,
                    Modifier.focusRequester(cardFocus).focusProperties {
                        canFocus = controlsUp
                        down = if (skipLabel != null) skipFocus else timelineFocus
                    },
                )
            }
        }
        AnimatedVisibility(skipLabel != null, enter = fadeIn(), exit = fadeOut()) {
            SkipButton(
                skipLabel ?: "", onClick = { if (s.skipIntroTo != null) vm.skipIntro() else vm.skipCredits() },
                Modifier.focusRequester(skipFocus).focusProperties {
                    canFocus = controlsUp
                    down = timelineFocus
                    if (card != null) up = cardFocus
                },
            )
        }
    }
}

/** The audio or subtitle list from the control row. Tracks this TV cannot decode are listed, not choosable. */
@Composable
private fun TrackPickerDialog(vm: PlayerViewModel, picker: TrackPicker, ps: PlayerUiState) {
    when (picker) {
        TrackPicker.Audio -> {
            val tracks = ps.audioTracks
            ChoiceListDialog(
                title = "Audio",
                choices = tracks.mapIndexed { i, tr -> ListChoice(i.toString(), tr.label + if (tr.supported) "" else UNSUPPORTED_TRACK, enabled = tr.supported) },
                selectedKey = tracks.indexOfFirst { it.selected }.takeIf { it >= 0 }?.toString(),
                subtitle = if (tracks.isEmpty()) "Audio track details are not available for this stream." else ps.audioNotice,
                onChoose = { key ->
                    val tr = tracks.getOrNull(key.toInt())
                    if (tr != null && !vm.player.selectTrack(tr, C.TRACK_TYPE_AUDIO)) vm.flashMessage("Audio tracks changed. Choose again.")
                    vm.closePicker()
                },
                onDismiss = vm::closePicker,
            )
        }
        TrackPicker.Subtitles -> {
            val tracks = ps.textTracks
            ChoiceListDialog(
                title = "Subtitles",
                choices = listOf(ListChoice(SUBTITLES_OFF, "Off")) + tracks.mapIndexed { i, tr -> ListChoice(i.toString(), tr.label + if (tr.supported) "" else UNSUPPORTED_TRACK, enabled = tr.supported) },
                selectedKey = if (ps.subtitlesOff) SUBTITLES_OFF else tracks.indexOfFirst { it.selected }.takeIf { it >= 0 }?.toString() ?: SUBTITLES_OFF,
                onChoose = { key ->
                    if (key == SUBTITLES_OFF) vm.player.disableSubtitles()
                    else tracks.getOrNull(key.toInt())?.let { vm.player.selectTrack(it, C.TRACK_TYPE_TEXT) }
                    vm.closePicker()
                },
                onDismiss = vm::closePicker,
            )
        }
    }
}

private const val SUBTITLES_OFF = "off"

/** OK while watching live: compact channel list overlay; focus starts on the playing channel. */
@Composable
private fun MiniList(channels: List<Channel>, currentId: String?, onPick: (Channel) -> Unit) {
    val t = LocalThemeTokens.current
    val g = glass
    val startIdx = channels.indexOfFirst { it.id == currentId }.coerceAtLeast(0)
    val state = rememberLazyListState(initialFirstVisibleItemIndex = (startIdx - 3).coerceAtLeast(0))
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { delay(50); runCatching { focus.requestFocus() } }
    GlassPanel(Modifier.fillMaxHeight().width(400.dp).padding(start = 14.dp, top = 14.dp, bottom = 14.dp), shape = RoundedCornerShape(20.dp), padding = PaddingValues(0.dp)) {
        PivotScroll(leadingOffset = 160.dp) {
            LazyColumn(
                state = state,
                modifier = Modifier.fillMaxSize().focusRestorer(focus),
                contentPadding = PaddingValues(vertical = Tokens.Canvas.SafeMarginY, horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                itemsIndexed(channels, key = { _, c -> c.id }) { i, ch ->
                    FocusSurface(
                        onClick = { onPick(ch) },
                        modifier = Modifier.fillMaxWidth().height(56.dp).then(if (i == startIdx) Modifier.focusRequester(focus) else Modifier),
                        shape = RoundedCornerShape(11.dp),
                        containerColor = if (ch.id == currentId) g.fillTop else Color.Transparent,
                        focusedContainerColor = g.rowFocus,
                        scaleOnFocus = false, glow = false,
                        ringColor = t.accent,
                    ) { focused ->
                        Row(Modifier.fillMaxSize().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(ch.number.toString(), color = if (focused) t.accent else g.muted, fontSize = 16.sp, fontWeight = FontWeight.Bold, modifier = Modifier.width(48.dp))
                            Column {
                                Text(ch.name + if (ch.id == currentId) "  ▶" else "", color = g.text, fontSize = 16.sp, fontWeight = if (focused) FontWeight.Bold else FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                ch.now?.let { Text(it.title, color = g.muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                            }
                        }
                    }
                }
            }
        }
    }
}

/**
 * "Stream info": the descriptor's media_info (source, container, codecs, resolution, delivery,
 * encoder) and Media3's own numbers (resolution, dropped frames, buffered seconds, bandwidth
 * estimate), read again every second while it is open. Back or Close returns to the menu.
 */
@Composable
private fun StreamInfoDialog(vm: PlayerViewModel, s: PlayerScreenState, onDismiss: () -> Unit) {
    val info = s.descriptor?.mediaInfo
    val lines by produceState(streamInfoLines(info, vm.player.stats()), info) {
        while (true) { value = streamInfoLines(info, vm.player.stats()); delay(1_000) }
    }
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        StreamInfoCard(lines, noDetails = info == null, onDismiss = onDismiss)
    }
}

/** Stream info's glass card: label and value in two columns, Close under them. */
@Composable
internal fun StreamInfoCard(lines: List<StreamInfoLine>, noDetails: Boolean, onDismiss: () -> Unit) {
    val g = glass
    val close = remember { FocusRequester() }
    GlassPanel(Modifier.width(620.dp), padding = PaddingValues(30.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Image(GlassIcons.Info, null, Modifier.size(26.dp), colorFilter = ColorFilter.tint(LocalThemeTokens.current.accent))
            DialogTitle("Stream info", maxLines = 1)
        }
        if (noDetails) DialogText("The server sent no details for this stream.", fontSize = 15.sp)
        Column(Modifier.padding(top = 6.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            lines.forEach { line ->
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    Text(line.label, Modifier.width(180.dp), color = g.dim, fontSize = 15.sp, maxLines = 1)
                    Text(line.value, Modifier.weight(1f), color = g.text, fontSize = 15.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
        }
        ShButton("Close", onDismiss, Modifier.padding(top = 10.dp).focusRequester(close))
    }
    LaunchedEffect(Unit) { withFrameNanos { }; runCatching { close.requestFocus() } }
}

/** Menu: audio track · subtitles · aspect ratio · sleep timer · stream info. Every track is listed; undecodable ones are disabled. */
@Composable
private fun PlayerMenu(vm: PlayerViewModel, s: PlayerScreenState, ps: PlayerUiState, canMinimise: Boolean) {
    val t = LocalThemeTokens.current
    val first = remember { FocusRequester() }
    val menuScope=rememberCoroutineScope()
    var confirmation by remember {mutableStateOf<TrackOption?>(null)}
    var confirmOpener by remember {mutableStateOf<FocusRequester?>(null)}
    var trackMessage by remember {mutableStateOf<String?>(null)}
    var streamInfo by remember { mutableStateOf(false) }
    val infoOpener = remember { FocusRequester() }
    var invite by remember { mutableStateOf(false) }
    val inviteOpener = remember { FocusRequester() }
    val dismissTrack:()->Unit={confirmation=null;menuScope.launch {withFrameNanos{};runCatching {(confirmOpener ?: first).requestFocus()}}}
    LaunchedEffect(ps.audioTracks) {
        if(confirmation!=null && confirmation !in ps.audioTracks) {confirmation=null;trackMessage="Audio tracks changed. Choose again.";withFrameNanos{};runCatching{first.requestFocus()}}
    }
    LaunchedEffect(Unit) { delay(50); runCatching { first.requestFocus() } }
    val room = s.room
    val inRoom = s.roomCode != null
    // No archive or Go Live in a room: it stays on the room clock.
    val isChannel = s.descriptor?.id?.startsWith("ch:") == true && !inRoom
    SettingsDrawer(
        SettingsModel(
            roomCode = s.roomCode,
            roomMembers = room?.members?.size ?: 0,
            roomLocked = room?.controlsLocked == true,
            roomHost = room?.isHost == true,
            channel = isChannel,
            hasBufferWindow = s.bufferWindow != null,
            bufferedSeconds = s.bufferWindow?.bufferedSeconds?.toInt(),
            rewindError = s.rewindError,
            bufferError = s.bufferError,
            audioNotice = ps.audioNotice,
            audioBlocked = ps.audioBlocked,
            trackMessage = trackMessage,
            audioTracks = ps.audioTracks,
            textTracks = ps.textTracks,
            subtitlesOff = ps.subtitlesOff,
            autoplay = s.autoplayNext.takeIf { s.upNext.kind != UpNextFlow.Kind.None },
            // Only where Back could minimise at all: a title outside a room, on a TV with picture-in-picture.
            backMinimises = s.backMinimises.takeIf { canMinimise && !inRoom && !s.live },
            aspectLabel = s.aspect.label,
            sleepOptions = PlayerViewModel.SLEEP_OPTIONS,
            sleepMinutes = s.sleepMinutes,
            watchTogether = vm.roomStart(s) != null,
            canRetry = ps.error != null,
        ),
        SettingsActions(
            onToggleRoomLock = vm::toggleRoomLock,
            onInvite = { invite = true },
            onLeaveRoom = vm::askLeaveRoom,
            onRewind = vm::openRewind,
            onGoLive = vm::goLive,
            onAudio = { tr, opener ->
                trackMessage = null
                if (vm.player.audioTrackNeedsConfirmation(tr)) { vm.player.pause(); confirmOpener = opener; confirmation = tr }
                else if (!vm.player.selectTrack(tr, C.TRACK_TYPE_AUDIO)) trackMessage = "Audio tracks changed. Choose again."
            },
            onSubtitlesOff = { vm.player.disableSubtitles() },
            onSubtitle = { tr -> vm.player.selectTrack(tr, C.TRACK_TYPE_TEXT) },
            onToggleAutoplay = { vm.setAutoplayNext(!s.autoplayNext) },
            onToggleBackMinimises = { vm.setBackMinimises(!s.backMinimises) },
            onAspect = vm::cycleAspect,
            onSleep = vm::setSleep,
            onWatchTogether = vm::startWatchTogether,
            onStreamInfo = { streamInfo = true },
            onRetry = vm::retry,
            onClose = vm::hideOverlay,
        ),
        first = first,
        streamInfoOpener = infoOpener,
        inviteOpener = inviteOpener,
    )
    if (streamInfo) StreamInfoDialog(vm, s) { streamInfo = false; menuScope.launch { withFrameNanos { }; runCatching { infoOpener.requestFocus() } } }
    if (invite && s.roomCode != null) InviteDialog(s.roomCode, s.roomShareUrl) { invite = false; menuScope.launch { withFrameNanos { }; runCatching { inviteOpener.requestFocus() } } }
    confirmation?.let {track ->
        val cancelFocus=remember {FocusRequester()}
        Dialog(onDismissRequest=dismissTrack, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            GlassPanel(Modifier.width(580.dp)) {
                DialogTitle("Use this audio track?")
                DialogText("${track.label} · ${track.language ?: "Language unverified"}. Its language is unverified or outside your preferred languages. Allow only this track for this playback?")
                ShButton("Cancel · keep paused",dismissTrack,Modifier.padding(top = 6.dp).focusRequester(cancelFocus), style = ButtonStyle.Primary)
                ShButton("Use this track once",{
                    if(vm.player.selectTrack(track,C.TRACK_TYPE_AUDIO,allowUnverifiedAudio=true)) {vm.player.resume();dismissTrack()}
                    else {trackMessage="Audio tracks changed. Choose again.";dismissTrack()}
                })
            }
        }
        LaunchedEffect(track){withFrameNanos{};runCatching{cancelFocus.requestFocus()}}
    }
}

/** Who is in the room: slim, top-left, shown with the controls and whenever the room is waiting. */
@Composable
private fun RoomStrip(code: String, room: RoomUiState, modifier: Modifier = Modifier) {
    Row(
        modifier.clip(RoundedCornerShape(Tokens.Radius.Pill)).background(Brush.verticalGradient(listOf(Color(0xCC181E34), Color(0xB3121729))))
            .border(1.dp, PictureGlass.Line, RoundedCornerShape(Tokens.Radius.Pill)).padding(horizontal = 16.dp, vertical = 7.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("Room ${RoomCode.display(code)}", color = Color.White, style = MaterialTheme.typography.labelMedium, maxLines = 1)
        room.members.take(MAX_STRIP_MEMBERS).forEach { m ->
            // Host and buffering are said with a mark, not with colour alone.
            Text(
                (if (m.host) "★ " else "") + m.name.ifBlank { "Guest" } + (if (m.buffering) " …" else ""),
                color = Color.White.copy(alpha = if (m.buffering) 0.6f else 0.9f), style = MaterialTheme.typography.labelMedium, maxLines = 1,
            )
        }
        if (room.members.size > MAX_STRIP_MEMBERS) Text("+${room.members.size - MAX_STRIP_MEMBERS}", color = Color.White.copy(alpha = 0.9f), style = MaterialTheme.typography.labelMedium)
        if (room.controlsLocked) Text("Host controls", color = Color.White.copy(alpha = 0.75f), style = MaterialTheme.typography.labelMedium, maxLines = 1)
    }
}

private const val MAX_STRIP_MEMBERS = 6

/** "Invite people" in a room: its code to read out and a QR code of its link, as when it was started. */
@Composable
private fun InviteDialog(code: String, shareUrl: String?, onDismiss: () -> Unit) {
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        InviteCard(code, shareUrl, onDismiss)
    }
}

@Composable
internal fun InviteCard(code: String, shareUrl: String?, onDismiss: () -> Unit) {
    val close = remember { FocusRequester() }
    GlassPanel(Modifier.width(840.dp), padding = PaddingValues(36.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(36.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Image(GlassIcons.People, null, Modifier.size(28.dp), colorFilter = ColorFilter.tint(LocalThemeTokens.current.accent))
                    DialogTitle("Invite people", maxLines = 1)
                }
                GroupLabel("Room code", Modifier.padding(top = 8.dp))
                BigCode(RoomCode.display(code))
                DialogText(
                    "On another TV: open Join a room from the menu and enter this code. On a phone or computer: scan the QR code" +
                        (shareUrl?.let { " or open $it" } ?: "") + ".",
                    fontSize = 16.sp,
                )
                ShButton("Done", onDismiss, Modifier.padding(top = 10.dp).focusRequester(close), style = ButtonStyle.Primary)
            }
            if (shareUrl != null) QrTile(shareUrl, size = 232.dp)
        }
    }
    LaunchedEffect(Unit) { withFrameNanos { }; runCatching { close.requestFocus() } }
}

/** Back in a room. Focus starts on staying: leaving is the choice that has to be made on purpose. */
@Composable
private fun LeaveRoomDialog(isHost: Boolean, onLeave: () -> Unit, onEnd: () -> Unit, onStay: () -> Unit) {
    Dialog(onDismissRequest = onStay, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        LeaveRoomCard(isHost, onLeave, onEnd, onStay)
    }
}

@Composable
internal fun LeaveRoomCard(isHost: Boolean, onLeave: () -> Unit, onEnd: () -> Unit, onStay: () -> Unit) {
    val stay = remember { FocusRequester() }
    GlassPanel(Modifier.width(600.dp)) {
        DialogTitle("Leave the room?")
        DialogText(
            if (isHost) "The others can keep watching without you, or you can end the room for everyone."
            else "The others keep watching. You can come back with the room's code while it is open.",
        )
        Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            ShButton("Stay", onStay, Modifier.focusRequester(stay), style = ButtonStyle.Primary)
            ShButton("Leave", onLeave, icon = GlassIcons.Leave)
            if (isHost) ShButton("End for everyone", onEnd, style = ButtonStyle.Danger)
        }
    }
    LaunchedEffect(Unit) { withFrameNanos { }; runCatching { stay.requestFocus() } }
}
