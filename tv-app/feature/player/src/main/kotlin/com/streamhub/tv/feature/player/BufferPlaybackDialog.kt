package com.streamhub.tv.feature.player

import android.view.KeyEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.Text
import com.streamhub.tv.core.player.PlayerUiState
import com.streamhub.tv.core.ui.components.DialogTitle
import com.streamhub.tv.core.ui.components.GlassPanel
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import java.time.Duration
import java.time.Instant

@Composable
internal fun BufferPlaybackDialog(vm: PlayerViewModel, state: PlayerScreenState, player: PlayerUiState) {
    val tokens = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    val retained = remember(state.bufferWindow) { WatchedTimeline.from(state.bufferWindow) }
    val window = retained ?: vm.localRewindRange()
    val local = retained == null && window != null
    val tune = state.descriptor?.url
    var selected by remember(tune, local) {
        val start = if (local) vm.localPlayhead(window).takeIf { Duration.between(it, window.end).seconds > 3 } else vm.bufferPlayhead()
        mutableStateOf(start)
    }
    val seek: (Instant?) -> Unit = { at -> if (local) vm.seekLocal(tune, at) else vm.seekBuffer(at) }
    val cursor = window?.let { range -> selected?.let(range::clamp) ?: range.end }
    Dialog(onDismissRequest = vm::hideOverlay) {
        GlassPanel(Modifier.width(640.dp), padding = PaddingValues(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            DialogTitle("Rewind this channel", maxLines = 1)
            if (window != null && cursor != null) {
                WatchedScrubber(window, cursor, focus, !state.bufferBusy,
                    onMove = { selected = window.seekAt(window.move(cursor, it)) },
                    onSelect = { seek(window.seekAt(cursor)) })
            } else {
                Text(if (state.bufferWindow != null || state.bufferBusy) "Waiting for the first recorded segment…" else "Rewind is unavailable for this tune.", color = tokens.textMuted)
            }
            state.bufferError?.let { Text(it, color = tokens.danger) }
            if (window != null || state.bufferWindow != null) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ShButton(if (player.isPlaying || vm.player.exoPlayer.playWhenReady) "Pause" else "Resume", vm::togglePlayPause)
                    ShButton("Go live", { seek(null) }, enabled = !state.bufferBusy)
                    if (state.bufferWindow != null) ShButton("Refresh", vm::refreshBuffer, enabled = !state.bufferBusy)
                }
            } else if (!state.bufferBusy) {
                ShButton("Retry", vm::retry)
            }
            ShButton("Close", vm::hideOverlay, if (window == null) Modifier.focusRequester(focus) else Modifier)
        }
    }
    LaunchedEffect(tune, state.bufferWindow != null, retained != null) {
        if (state.bufferWindow != null && retained == null) {
            kotlinx.coroutines.delay(3000)
            vm.refreshBuffer()
        }
    }
    LaunchedEffect(tune, window != null) {
        withFrameNanos { }
        runCatching { focus.requestFocus() }
    }
}

@Composable
internal fun WatchedScrubber(range: WatchedTimeline, cursor: Instant, focus: FocusRequester, enabled: Boolean,
                             onMove: (Long) -> Unit, onSelect: () -> Unit) {
    val t = LocalThemeTokens.current
    val progress = range.progress(cursor)
    val behind = Duration.between(cursor, range.end).seconds
    val label = if (cursor == range.end) "Live" else "${clock(behind * 1000)} behind live"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text("${clock(range.durationMs)} ago", color = t.textMuted)
        Text("Live", color = t.textMuted)
    }
    Box(Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(3.dp)).background(t.textMuted.copy(alpha = .3f))
        .semantics { progressBarRangeInfo = ProgressBarRangeInfo(progress, 0f..1f) }) {
        Box(Modifier.fillMaxWidth(progress).fillMaxHeight().background(t.focusRing))
    }
    ShButton(label, onSelect, Modifier.focusRequester(focus).onPreviewKeyEvent { e ->
        val dir = when (e.nativeKeyEvent.keyCode) {
            KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_MEDIA_REWIND -> -1
            KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> 1
            else -> return@onPreviewKeyEvent false
        }
        if (enabled && e.type == KeyEventType.KeyDown) onMove(dir * PlayerKeyMap.seekStepMs(e.nativeKeyEvent.repeatCount))
        true
    }, enabled = enabled)
    Text("Left or right to move · Hold to scan faster · OK to play", color = t.textMuted)
}
