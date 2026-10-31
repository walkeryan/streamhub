package com.streamhub.tv.feature.player

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
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
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.player.PlayerUiState
import com.streamhub.tv.core.player.VideoSurface
import com.streamhub.tv.core.ui.components.ProgressLine
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.components.programmeProgress
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.PivotScroll
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

private fun isOk(key: Key) = key == Key.DirectionCenter || key == Key.Enter || key == Key.NumPadEnter

/** Tracks one OK press across its KeyDown / auto-repeat / KeyUp events (not state: no recomposition). */
private class OkPress {
    var down = false
    var longHandled = false
}

private fun digitOf(key: Key): Int? = when (key.nativeKeyCode) {
    in android.view.KeyEvent.KEYCODE_0..android.view.KeyEvent.KEYCODE_9 -> key.nativeKeyCode - android.view.KeyEvent.KEYCODE_0
    in android.view.KeyEvent.KEYCODE_NUMPAD_0..android.view.KeyEvent.KEYCODE_NUMPAD_9 -> key.nativeKeyCode - android.view.KeyEvent.KEYCODE_NUMPAD_0
    else -> null
}

/**
 * Remote mapping
 *  Live:  Up/Down, CH+/CH- = zap · 0-9 = channel number (commits after 2 s or OK) · OK = mini channel list
 *         Back = exit; Back twice within 500 ms = last channel
 *  VOD:   OK / Play-Pause = toggle · Left/Right / FF/RW = seek ∓10 s/30 s · Up/Down = show OSD
 *  Both:  hold OK (or Menu/Settings key) = audio/subtitles/aspect/sleep menu. Most Google TV remotes
 *         have no Menu key, so the short-press OK action fires on key *up* and a hold opens the menu.
 */
@Composable
fun PlayerScreen(onExit: () -> Unit, vm: PlayerViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val ps by vm.player.state.collectAsStateWithLifecycle()
    val rootFocus = remember { FocusRequester() }
    val scope = rememberCoroutineScope()
    var lastBackAt by remember { mutableLongStateOf(0L) }
    var pendingExit by remember { mutableStateOf<Job?>(null) }
    val ok = remember { OkPress() }

    LaunchedEffect(Unit) { vm.exit.collect { onExit() } }
    LaunchedEffect(s.overlay) {
        if (s.overlay == Overlay.None || s.overlay == Overlay.Osd) runCatching { rootFocus.requestFocus() }
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black)
            .focusRequester(rootFocus)
            .onPreviewKeyEvent { e ->
                val overlayOpen = s.overlay == Overlay.MiniList || s.overlay == Overlay.Menu
                if (e.key == Key.Back) {
                    if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent true
                    when {
                        overlayOpen -> vm.hideOverlay()
                        s.numberEntry.isNotEmpty() -> vm.commitNumber()
                        s.live && vm.hasLastChannel -> {
                            val now = System.currentTimeMillis()
                            if (now - lastBackAt < PlayerViewModel.BACK_DOUBLE_MS) {
                                pendingExit?.cancel(); lastBackAt = 0; vm.lastChannel()
                            } else {
                                lastBackAt = now
                                pendingExit = scope.launch { delay(PlayerViewModel.BACK_DOUBLE_MS); onExit() }
                            }
                        }
                        else -> onExit()
                    }
                    return@onPreviewKeyEvent true
                }
                // OK / D-pad centre: short press acts on key up; holding it opens the menu. Every edge of a
                // press that started here is consumed, including the KeyUp after the menu has opened, so the
                // release can't click the menu item that just took focus.
                if (isOk(e.key) && (ok.down || (!overlayOpen && e.type == KeyEventType.KeyDown))) {
                    val n = e.nativeKeyEvent
                    when (e.type) {
                        KeyEventType.KeyDown -> when {
                            !ok.down -> { ok.down = true; ok.longHandled = false }
                            !ok.longHandled && (n.isLongPress || n.eventTime - n.downTime >= LONG_PRESS_MS) -> {
                                ok.longHandled = true; vm.openMenu()
                            }
                        }
                        KeyEventType.KeyUp -> {
                            val wasLong = ok.longHandled || n.eventTime - n.downTime >= LONG_PRESS_MS
                            val handled = ok.longHandled
                            ok.down = false; ok.longHandled = false
                            when {
                                handled -> Unit
                                wasLong -> vm.openMenu() // remotes that send no auto-repeat while held
                                vm.commitNumber() -> Unit
                                s.live -> vm.openMiniList()
                                else -> { vm.player.togglePlayPause(); vm.showOsd() }
                            }
                        }
                    }
                    return@onPreviewKeyEvent true
                }
                if (overlayOpen) return@onPreviewKeyEvent false // let the overlay's focusables handle D-pad
                if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                digitOf(e.key)?.let { vm.digit(it); return@onPreviewKeyEvent true }
                when (e.key) {
                    Key.DirectionUp, Key.ChannelUp -> { if (s.live) vm.zap(+1) else vm.showOsd(); true }
                    Key.DirectionDown, Key.ChannelDown -> { if (s.live) vm.zap(-1) else vm.showOsd(); true }
                    Key.DirectionLeft -> { vm.player.seekBy(-10_000); vm.showOsd(); true }
                    Key.DirectionRight -> { vm.player.seekBy(10_000); vm.showOsd(); true }
                    Key.MediaRewind -> { vm.player.seekBy(-30_000); vm.showOsd(); true }
                    Key.MediaFastForward -> { vm.player.seekBy(30_000); vm.showOsd(); true }
                    Key.MediaPlayPause -> { vm.player.togglePlayPause(); vm.showOsd(); true }
                    Key.MediaPlay -> { vm.player.exoPlayer.play(); vm.showOsd(); true }
                    Key.MediaPause -> { vm.player.exoPlayer.pause(); vm.showOsd(); true }
                    Key.Menu, Key.Settings -> { vm.openMenu(); true }
                    Key.Info, Key.Guide -> { vm.showOsd(); true }
                    Key.LastChannel -> { vm.lastChannel(); true }
                    else -> false
                }
            }
            .focusable(),
    ) {
        VideoSurface(vm.player, Modifier.fillMaxSize(), s.aspect)

        if (ps.buffering || ps.error != null || s.message != null) {
            StatusBadge(ps.error ?: s.message ?: "Buffering…", Modifier.align(Alignment.Center))
        }

        AnimatedVisibility(s.overlay == Overlay.Osd || s.numberEntry.isNotEmpty(), enter = fadeIn(), exit = fadeOut(), modifier = Modifier.align(Alignment.BottomCenter)) {
            Osd(s, ps)
        }
        if (s.numberEntry.isNotEmpty()) NumberEntry(s.numberEntry, s.channels, Modifier.align(Alignment.TopEnd))

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
        if (s.live) {
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
            if (s.live) "▲▼ zap · 0–9 channel · OK list · hold OK menu · Back×2 last channel" else "OK play/pause · ◀▶ seek · hold OK menu",
            style = MaterialTheme.typography.labelMedium, color = Color.White.copy(alpha = 0.7f), modifier = Modifier.padding(top = 10.dp),
        )
        s.sleepEndsAt?.let {
            val min = ((it - System.currentTimeMillis()) / 60_000).coerceAtLeast(0)
            Text("Sleep in $min min", style = MaterialTheme.typography.labelMedium, color = t.accent)
        }
    }
}

@Composable
private fun NumberEntry(entry: String, channels: List<Channel>, modifier: Modifier) {
    val t = LocalThemeTokens.current
    val match = entry.toIntOrNull()?.let { n -> channels.firstOrNull { it.number == n } }
    Column(modifier.padding(top = Tokens.Canvas.SafeMarginY, end = Tokens.Canvas.SafeMarginX).background(t.overlay, RoundedCornerShape(Tokens.Radius.Md)).padding(20.dp), horizontalAlignment = Alignment.End) {
        Text(entry, style = MaterialTheme.typography.displayMedium, color = Color.White)
        Text(match?.name ?: "—", style = MaterialTheme.typography.bodyMedium, color = Color.White.copy(alpha = 0.8f))
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

/** Menu: audio track · subtitles · aspect ratio · sleep timer. */
@Composable
private fun PlayerMenu(vm: PlayerViewModel, s: PlayerScreenState, ps: PlayerUiState) {
    val t = LocalThemeTokens.current
    val first = remember { FocusRequester() }
    LaunchedEffect(Unit) { delay(50); runCatching { first.requestFocus() } }
    Column(
        Modifier.fillMaxHeight().width(420.dp).background(t.overlay).padding(horizontal = 24.dp, vertical = Tokens.Canvas.SafeMarginY),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text("Audio", style = MaterialTheme.typography.titleSmall, color = t.text)
        if (ps.audioTracks.isEmpty()) Text("Default", color = t.textMuted, style = MaterialTheme.typography.bodySmall)
        ps.audioTracks.forEachIndexed { i, tr ->
            ShChip(tr.label, tr.selected, { vm.player.selectTrack(tr, C.TRACK_TYPE_AUDIO) }, if (i == 0) Modifier.focusRequester(first) else Modifier)
        }
        Text("Subtitles", style = MaterialTheme.typography.titleSmall, color = t.text, modifier = Modifier.padding(top = 8.dp))
        ShChip("Off", ps.subtitlesOff, { vm.player.disableSubtitles() }, if (ps.audioTracks.isEmpty()) Modifier.focusRequester(first) else Modifier)
        ps.textTracks.forEach { tr -> ShChip(tr.label, !ps.subtitlesOff && tr.selected, { vm.player.selectTrack(tr, C.TRACK_TYPE_TEXT) }) }
        Text("Picture", style = MaterialTheme.typography.titleSmall, color = t.text, modifier = Modifier.padding(top = 8.dp))
        ShButton("Aspect: ${s.aspect.label}", vm::cycleAspect)
        Text("Sleep timer", style = MaterialTheme.typography.titleSmall, color = t.text, modifier = Modifier.padding(top = 8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            PlayerViewModel.SLEEP_OPTIONS.forEach { m -> ShChip(m?.let { "$it" } ?: "Off", s.sleepMinutes == m, { vm.setSleep(m) }) }
        }
        if (ps.error != null) ShButton("Retry stream", vm::retry)
    }
}
