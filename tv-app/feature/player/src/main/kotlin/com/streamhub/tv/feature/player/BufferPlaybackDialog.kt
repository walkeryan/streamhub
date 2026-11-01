package com.streamhub.tv.feature.player

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.repo.retainedBufferChoices
import com.streamhub.tv.core.player.PlayerUiState
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

@Composable
internal fun BufferPlaybackDialog(vm: PlayerViewModel, state: PlayerScreenState, player: PlayerUiState) {
    val t=LocalThemeTokens.current
    val close=remember { FocusRequester() }
    val window=state.bufferWindow
    val choices=remember(window) { window?.let(::retainedBufferChoices).orEmpty() }
    val fmt=remember { DateTimeFormatter.ofPattern("HH:mm:ss 'UTC'").withZone(ZoneOffset.UTC) }
    Dialog(onDismissRequest=vm::hideOverlay) {
        Column(Modifier.width(640.dp).heightIn(max=560.dp).background(t.surface,RoundedCornerShape(20.dp)).padding(24.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
            Text("Buffered rewind",color=t.text,style=MaterialTheme.typography.titleLarge)
            Text(if(window==null) "No retained window is available." else "${window.bufferedSeconds.toInt()} seconds retained. History grows while watched; 60 minutes is a target, not available at startup.",color=t.textMuted)
            window?.let { Text("${it.start ?: "Waiting for first segment"} to ${it.end ?: "—"}",color=t.textMuted) }
            Text("Pause is limited to one hour. Leaving playback releases this viewer's buffer lease.",color=t.textMuted)
            ShButton("Close",vm::hideOverlay,Modifier.focusRequester(close))
            state.bufferError?.let { Text(it,color=t.danger) }
            if(window!=null) {
                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    ShButton(if(vm.player.exoPlayer.playWhenReady) "Pause" else "Resume",{vm.player.togglePlayPause()})
                    ShButton("Go Live",vm::goLive,enabled=!state.bufferBusy)
                    ShButton("Refresh window",vm::refreshBuffer,enabled=!state.bufferBusy)
                }
                Text(if(player.seekable) "Left/right in playback seeks within the actual player timeline." else "Fine seeking is not available on the current player timeline.",color=t.textMuted)
            } else {
                ShButton("Retry buffer / Play",vm::retry,enabled=!state.bufferBusy)
                ShButton("Play live without buffering",vm::playWithoutBuffer,enabled=!state.bufferBusy)
            }
            LazyColumn(Modifier.weight(1f,fill=false),verticalArrangement=Arrangement.spacedBy(8.dp)) {
                items(choices,key={it.toString()}) { at -> ShButton("Watch from ${fmt.format(at)}",{vm.seekBuffer(at)},enabled=!state.bufferBusy) }
            }
        }
    }
    LaunchedEffect(Unit) { runCatching { close.requestFocus() } }
}
