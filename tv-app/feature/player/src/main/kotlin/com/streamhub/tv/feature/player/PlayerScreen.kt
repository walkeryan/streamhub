package com.streamhub.tv.feature.player

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
import com.streamhub.tv.core.player.TrackOption
import androidx.compose.runtime.Composable
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
import com.streamhub.tv.core.player.PlayerUiState
import com.streamhub.tv.core.player.VideoSurface
import com.streamhub.tv.core.ui.components.ProgressLine
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
private const val UNSUPPORTED_TRACK = " (not supported on this TV)"

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
 */
@Composable
fun PlayerScreen(onExit: () -> Unit, vm: PlayerViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val ps by vm.player.state.collectAsStateWithLifecycle()
    val rootFocus = remember { FocusRequester() }
    val scope = rememberCoroutineScope()
    var lastBackAt by remember { mutableLongStateOf(0L) }
    var pendingExit by remember { mutableStateOf<Job?>(null) }
    var backHint by remember { mutableStateOf(false) }
    var backHintShown by remember { mutableStateOf(false) }
    val ok = remember { OkPress() }

    LaunchedEffect(Unit) { vm.exit.collect { onExit() } }
    LaunchedEffect(ps.audioBlocked) {if(ps.audioBlocked) vm.openMenu()}
    LaunchedEffect(s.overlay) {
        if (s.overlay == Overlay.None || s.overlay == Overlay.Osd) runCatching { rootFocus.requestFocus() }
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
            PlayerCommand.TogglePlayPause -> { vm.player.togglePlayPause(); vm.showOsd() }
            PlayerCommand.Play -> { vm.player.resume(); vm.showOsd() }
            PlayerCommand.Pause -> { vm.player.pause(); vm.showOsd() }
            is PlayerCommand.Seek -> { vm.scrubBy(command.deltaMs); vm.showOsd() }
            PlayerCommand.LastChannel -> vm.lastChannel()
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
                // Every press counts as activity for the control row's 5 s idle timer.
                if (e.type == KeyEventType.KeyDown) vm.touchControls()
                if (PlayerKeyMap.resolve(code, live, menuOpen, controls) == PlayerCommand.Back) {
                    if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent true
                    when {
                        menuOpen || controls -> vm.hideOverlay()
                        s.numberEntry.isNotEmpty() -> vm.commitNumber()
                        s.live && vm.hasLastChannel -> {
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
                        else -> onExit()
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
                                ok.longHandled = true; perform(PlayerKeyMap.resolve(code, live, menuOpen, controls, longPress = true))
                            }
                        }
                        KeyEventType.KeyUp -> {
                            val wasLong = ok.longHandled || n.eventTime - n.downTime >= LONG_PRESS_MS
                            val handled = ok.longHandled
                            ok.down = false; ok.longHandled = false
                            when {
                                handled -> Unit
                                // remotes that send no auto-repeat while held
                                wasLong -> perform(PlayerKeyMap.resolve(code, live, menuOpen, controls, longPress = true))
                                vm.commitNumber() -> Unit
                                else -> perform(PlayerKeyMap.resolve(code, live, menuOpen, controls))
                            }
                        }
                    }
                    return@onPreviewKeyEvent true
                }
                if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                // None = the overlay's or control row's focusables handle the key.
                val command = PlayerKeyMap.resolve(code, live, menuOpen, controls)
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
            s.retrying -> "Trying again…"
            failure != null -> null
            ps.error != null -> ps.error
            s.message != null -> s.message
            s.bufferBusy -> "Preparing buffer…"
            ps.buffering -> "Buffering…"
            else -> null
        }
        if (status != null) StatusBadge(status, Modifier.align(Alignment.Center))
        if (backHint) StatusBadge("Back again for last channel", Modifier.align(Alignment.TopCenter).padding(top = Tokens.Canvas.SafeMarginY))
        if (failure != null && s.audioChoice == null) {
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
        AnimatedVisibility(s.overlay == Overlay.Controls && s.numberEntry.isEmpty(), enter = fadeIn(), exit = fadeOut(), modifier = Modifier.align(Alignment.BottomCenter)) {
            FilmControls(vm, s, ps)
        }
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
            PlayerMenu(vm, s, ps)
        }
    }
}

@Composable
private fun StatusBadge(text: String, modifier: Modifier) {
    val t = LocalThemeTokens.current
    Text(text, color = t.text, style = MaterialTheme.typography.bodyLarge, modifier = modifier.background(t.overlay, RoundedCornerShape(Tokens.Radius.Md)).padding(horizontal = 20.dp, vertical = 12.dp))
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
            if (ps.durationMs > 0) {
                ProgressLine(ps.positionMs.toFloat() / ps.durationMs, Modifier.padding(top = 12.dp))
                Row(Modifier.fillMaxWidth().padding(top = 4.dp)) {
                    Text(fmt(ps.positionMs), color = Color.White, style = MaterialTheme.typography.labelLarge)
                    Spacer(Modifier.weight(1f))
                    Text(if (ps.isPlaying) "▶" else "❚❚", color = Color.White, style = MaterialTheme.typography.labelLarge)
                    Spacer(Modifier.weight(1f))
                    Text("-" + fmt(ps.durationMs - ps.positionMs), color = Color.White, style = MaterialTheme.typography.labelLarge)
                }
            }
        }
        Text(
            if (s.live && !s.bufferTimeshift) "▲▼ zap · OK list · ◀ archive · ▶ menu · Back×2 last channel" else "OK or ▼ for playback controls",
            style = MaterialTheme.typography.labelMedium, color = Color.White.copy(alpha = 0.7f), modifier = Modifier.padding(top = 10.dp),
        )
        ps.audioNotice?.let {Text(it,color=t.textMuted,style=MaterialTheme.typography.labelMedium)}
        s.sleepEndsAt?.let {
            val min = ((it - System.currentTimeMillis()) / 60_000).coerceAtLeast(0)
            Text("Sleep in $min min", style = MaterialTheme.typography.labelMedium, color = t.accent)
        }
    }
}

/**
 * Film control row: the scrubber has focus first, the buttons sit one Down below it. Everything a
 * film needs is reachable with the D-pad and Select; nothing here depends on a media or Menu key.
 */
@Composable
private fun FilmControls(vm: PlayerViewModel, s: PlayerScreenState, ps: PlayerUiState) {
    val t = LocalThemeTokens.current
    val d = s.descriptor
    val scrubFocus = remember { FocusRequester() }
    LaunchedEffect(Unit) { withFrameNanos { }; runCatching { scrubFocus.requestFocus() } }
    Column(
        Modifier.fillMaxWidth()
            .background(Brush.verticalGradient(0f to Color.Transparent, 1f to Color.Black.copy(alpha = 0.92f)))
            .padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = Tokens.Canvas.SafeMarginY)
            .padding(top = 64.dp)
            .focusGroup(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(d?.title.orEmpty(), style = MaterialTheme.typography.headlineSmall, color = Color.White, maxLines = 1, overflow = TextOverflow.Ellipsis)
        d?.subtitle?.let { Text(it, style = MaterialTheme.typography.bodyLarge, color = Color.White.copy(alpha = 0.8f), maxLines = 1, overflow = TextOverflow.Ellipsis) }
        Scrubber(
            positionMs = s.scrubTargetMs ?: ps.positionMs,
            durationMs = ps.durationMs,
            playing = ps.isPlaying,
            seekable = ps.seekable,
            onSeek = vm::scrubBy,
            onToggle = { vm.player.togglePlayPause() },
            modifier = Modifier.focusRequester(scrubFocus),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            ShButton(if (ps.isPlaying) "Pause" else "Play", { vm.player.togglePlayPause() })
            ShButton("−10 s", { vm.scrubBy(-PlayerKeyMap.SEEK_STEP_MS) }, enabled = ps.seekable)
            ShButton("+10 s", { vm.scrubBy(PlayerKeyMap.SEEK_STEP_MS) }, enabled = ps.seekable)
            ShButton("Start over", vm::startOver, enabled = ps.seekable)
            ShButton("Audio & subtitles", vm::openMenu)
        }
        Text(
            when {
                s.bufferTimeshift -> "Buffered history · Go Live is under Audio & subtitles"
                d?.archiveStart != null -> "Provider archive · Go Live is under Audio & subtitles"
                !ps.seekable -> "This stream can't be seeked"
                else -> "◀ ▶ seek, hold to go faster · Select play/pause"
            },
            style = MaterialTheme.typography.labelMedium, color = Color.White.copy(alpha = 0.7f),
        )
        ps.audioNotice?.let { Text(it, color = t.textMuted, style = MaterialTheme.typography.labelMedium) }
        s.sleepEndsAt?.let {
            val min = ((it - System.currentTimeMillis()) / 60_000).coerceAtLeast(0)
            Text("Sleep in $min min", style = MaterialTheme.typography.labelMedium, color = t.accent)
        }
    }
}

/** Focusable progress bar: Left/Right seek (faster while held), Select toggles play/pause. */
@Composable
private fun Scrubber(
    positionMs: Long, durationMs: Long, playing: Boolean, seekable: Boolean,
    onSeek: (Long) -> Unit, onToggle: () -> Unit, modifier: Modifier = Modifier,
) {
    FocusSurface(
        onClick = onToggle,
        modifier = modifier.fillMaxWidth().height(44.dp).onPreviewKeyEvent { e ->
            val direction = when (e.key) { Key.DirectionLeft -> -1; Key.DirectionRight -> 1; else -> 0 }
            if (direction == 0) false else {
                if (e.type == KeyEventType.KeyDown && seekable) onSeek(direction * PlayerKeyMap.seekStepMs(e.nativeKeyEvent.repeatCount))
                // Consumed even at either end of the film: there is nothing beside the scrubber to move to.
                true
            }
        },
        containerColor = Color.Transparent,
        focusedContainerColor = Color.White.copy(alpha = 0.14f),
        scaleOnFocus = false,
        glow = false,
    ) { focused ->
        Row(Modifier.fillMaxSize().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(if (playing) "▶" else "❚❚", color = Color.White, style = MaterialTheme.typography.labelLarge)
            Text(fmt(positionMs), color = Color.White, style = MaterialTheme.typography.labelLarge)
            ProgressLine(if (durationMs > 0) positionMs.toFloat() / durationMs else 0f, Modifier.weight(1f), height = if (focused) 8.dp else 4.dp)
            if (durationMs > 0) Text("-" + fmt((durationMs - positionMs).coerceAtLeast(0)), color = Color.White, style = MaterialTheme.typography.labelLarge)
        }
    }
}

/** OK while watching live: compact channel list overlay; focus starts on the playing channel. */
@Composable
private fun MiniList(channels: List<Channel>, currentId: String?, onPick: (Channel) -> Unit) {
    val t = LocalThemeTokens.current
    val startIdx = channels.indexOfFirst { it.id == currentId }.coerceAtLeast(0)
    val state = rememberLazyListState(initialFirstVisibleItemIndex = (startIdx - 3).coerceAtLeast(0))
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { delay(50); runCatching { focus.requestFocus() } }
    Box(Modifier.fillMaxHeight().width(380.dp).background(t.overlay)) {
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
                        modifier = Modifier.fillMaxWidth().height(52.dp).then(if (i == startIdx) Modifier.focusRequester(focus) else Modifier),
                        containerColor = if (ch.id == currentId) t.surfaceAlt else Color.Transparent,
                        focusedContainerColor = t.text,
                        scaleOnFocus = false, glow = false,
                    ) { focused ->
                        val fg = if (focused) t.bg else t.text
                        Row(Modifier.fillMaxSize().padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(ch.number.toString(), color = fg, style = MaterialTheme.typography.labelLarge, modifier = Modifier.width(48.dp))
                            Column {
                                Text(ch.name + if (ch.id == currentId) "  ▶" else "", color = fg, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                ch.now?.let { Text(it.title, color = fg.copy(alpha = 0.75f), style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Menu: audio track · subtitles · aspect ratio · sleep timer. Every track is listed; undecodable ones are disabled. */
@Composable
private fun PlayerMenu(vm: PlayerViewModel, s: PlayerScreenState, ps: PlayerUiState) {
    val t = LocalThemeTokens.current
    val first = remember { FocusRequester() }
    val menuScope=rememberCoroutineScope()
    var confirmation by remember {mutableStateOf<TrackOption?>(null)}
    var confirmOpener by remember {mutableStateOf<FocusRequester?>(null)}
    var trackMessage by remember {mutableStateOf<String?>(null)}
    val dismissTrack:()->Unit={confirmation=null;menuScope.launch {withFrameNanos{};runCatching {(confirmOpener ?: first).requestFocus()}}}
    LaunchedEffect(ps.audioTracks) {
        if(confirmation!=null && confirmation !in ps.audioTracks) {confirmation=null;trackMessage="Audio tracks changed. Choose again.";withFrameNanos{};runCatching{first.requestFocus()}}
    }
    LaunchedEffect(Unit) { delay(50); runCatching { first.requestFocus() } }
    val isChannel = s.descriptor?.id?.startsWith("ch:") == true
    // Initial focus must land on something focusable: tracks this TV cannot decode are listed but disabled.
    val firstAudio = ps.audioTracks.indexOfFirst { it.supported }
    Column(
        Modifier.fillMaxHeight().width(420.dp).background(t.overlay).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = Tokens.Canvas.SafeMarginY),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if(isChannel) {
            ShButton(if(s.bufferWindow!=null) "Buffered rewind" else "Provider archive · unverified", vm::openRewind, Modifier.focusRequester(first))
            ShButton("Go Live", vm::goLive)
            s.rewindError?.let { Text(it, color=t.danger) }
            s.bufferWindow?.let { Text("${it.bufferedSeconds.toInt()} seconds retained · grows while watched",color=t.textMuted) }
            s.bufferError?.let { Text(it,color=t.danger) }
        }
        Text("Audio", style = MaterialTheme.typography.titleSmall, color = t.text)
        ps.audioNotice?.let {Text(it,color=if(ps.audioBlocked)t.danger else t.textMuted)}
        trackMessage?.let {Text(it,color=t.textMuted)}
        if (ps.audioTracks.isEmpty()) Text("Audio track metadata unavailable", color = t.textMuted, style = MaterialTheme.typography.bodySmall)
        ps.audioTracks.forEachIndexed { i, tr ->
            val opener=remember(tr.revision,tr.groupIndex,tr.trackIndex){FocusRequester()}
            val note = when {
                !tr.supported -> UNSUPPORTED_TRACK
                tr.language.isNullOrBlank() || tr.language.equals("und",true) -> " · language unverified"
                else -> ""
            }
            ShChip(tr.label + note, tr.selected, {
                trackMessage=null
                if(vm.player.audioTrackNeedsConfirmation(tr)) {vm.player.pause();confirmOpener=opener;confirmation=tr}
                else if(!vm.player.selectTrack(tr,C.TRACK_TYPE_AUDIO)) trackMessage="Audio tracks changed. Choose again."
            },Modifier.focusRequester(opener).then(if(i==firstAudio && !isChannel)Modifier.focusRequester(first) else Modifier), enabled = tr.supported)
        }
        Text("Subtitles", style = MaterialTheme.typography.titleSmall, color = t.text, modifier = Modifier.padding(top = 8.dp))
        ShChip("Off", ps.subtitlesOff, { vm.player.disableSubtitles() }, if (firstAudio < 0 && !isChannel) Modifier.focusRequester(first) else Modifier)
        ps.textTracks.forEach { tr ->
            ShChip(tr.label + if (tr.supported) "" else UNSUPPORTED_TRACK, !ps.subtitlesOff && tr.selected, { vm.player.selectTrack(tr, C.TRACK_TYPE_TEXT) }, enabled = tr.supported)
        }
        Text("Picture", style = MaterialTheme.typography.titleSmall, color = t.text, modifier = Modifier.padding(top = 8.dp))
        ShButton("Aspect: ${s.aspect.label}", vm::cycleAspect)
        Text("Sleep timer", style = MaterialTheme.typography.titleSmall, color = t.text, modifier = Modifier.padding(top = 8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            PlayerViewModel.SLEEP_OPTIONS.forEach { m -> ShChip(m?.let { "$it" } ?: "Off", s.sleepMinutes == m, { vm.setSleep(m) }) }
        }
        if (ps.error != null) ShButton("Retry stream", vm::retry)
        ShButton("Close controls",vm::hideOverlay)
    }
    confirmation?.let {track ->
        val cancelFocus=remember {FocusRequester()}
        Dialog(onDismissRequest=dismissTrack) {
            Column(Modifier.width(520.dp).background(t.surface,RoundedCornerShape(20.dp)).padding(24.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                Text("Use this audio track?",color=t.text,style=MaterialTheme.typography.titleLarge)
                Text("${track.label} · ${track.language ?: "Language unverified"}. Its language is unverified or outside your preferred languages. Allow only this track for this playback?",color=t.textMuted)
                ShButton("Cancel · keep paused",dismissTrack,Modifier.focusRequester(cancelFocus))
                ShButton("Use this track once",{
                    if(vm.player.selectTrack(track,C.TRACK_TYPE_AUDIO,allowUnverifiedAudio=true)) {vm.player.resume();dismissTrack()}
                    else {trackMessage="Audio tracks changed. Choose again.";dismissTrack()}
                })
            }
        }
        LaunchedEffect(track){withFrameNanos{};runCatching{cancelFocus.requestFocus()}}
    }
}
