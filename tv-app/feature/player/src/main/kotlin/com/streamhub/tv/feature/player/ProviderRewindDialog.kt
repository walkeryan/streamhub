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
import com.streamhub.tv.core.data.repo.archiveChoices
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

@Composable
internal fun ProviderRewindDialog(vm: PlayerViewModel, state: PlayerScreenState) {
    val t = LocalThemeTokens.current
    val close = remember { FocusRequester() }
    val capability = state.rewind
    val choices = remember(capability) { capability?.let(::archiveChoices).orEmpty() }
    val format = remember { DateTimeFormatter.ofPattern("HH:mm 'UTC'").withZone(ZoneOffset.UTC) }
    Dialog(onDismissRequest = vm::hideOverlay) {
        Column(Modifier.width(600.dp).heightIn(max=560.dp).background(t.surface, RoundedCornerShape(20.dp)).padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Provider archive", style=MaterialTheme.typography.titleLarge, color=t.text)
            Text("Advertised, not verified. No local recording buffer. Provider media may be unavailable.", color=t.textMuted)
            ShButton("Cancel · keep current stream", vm::hideOverlay, Modifier.focusRequester(close))
            ShButton("Go Live", vm::goLive, enabled=!state.rewindLoading)
            if(state.rewindLoading) Text("Checking provider…", color=t.textMuted)
            state.rewindError?.let { Text(it, color=t.danger) }
            capability?.let {
                Text(it.reason, color=t.textMuted)
                if(choices.isNotEmpty()) Text("${it.advertisedStart} to ${it.advertisedEnd} · 1-minute choices", color=t.textMuted)
            }
            ShButton("Refresh advertised window", vm::openRewind, enabled=!state.rewindLoading)
            LazyColumn(Modifier.weight(1f, fill=false), verticalArrangement=Arrangement.spacedBy(8.dp)) {
                items(choices, key={it.toString()}) { instant ->
                    ShButton("Try archive from ${format.format(instant)}", { vm.playArchive(instant) }, enabled=!state.rewindLoading)
                }
            }
        }
    }
    LaunchedEffect(Unit) { runCatching { close.requestFocus() } }
}
