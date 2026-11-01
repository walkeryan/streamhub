package com.streamhub.tv.core.ui.components

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
import com.streamhub.tv.core.data.model.PlaybackOption
import com.streamhub.tv.core.data.model.PlaybackOptions
import com.streamhub.tv.core.ui.theme.LocalThemeTokens

/** Every action is reachable with four-way D-pad, Select and Back. Consent is per playback only. */
@Composable
fun AudioSourceDialog(options: PlaybackOptions, onDismiss: () -> Unit, confirmLabel:String="Play this source once", onChoose: (String, Boolean) -> Unit) {
    val t = LocalThemeTokens.current
    var pending by remember(options) { mutableStateOf<PlaybackOption?>(null) }
    val cancelFocus = remember { FocusRequester() }
    Dialog(onDismissRequest = { if (pending != null) pending = null else onDismiss() }) {
        Column(Modifier.width(640.dp).heightIn(max = 560.dp).background(t.surface, RoundedCornerShape(20.dp)).padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(if (pending == null) "Choose audio / source" else "Confirm audio choice", style = MaterialTheme.typography.titleLarge, color = t.text)
            ShButton(if (pending == null) "Cancel" else "Back to sources", { if (pending != null) pending = null else onDismiss() }, Modifier.focusRequester(cancelFocus))
            val selected = pending
            if (selected != null) {
                Text("This source has unverified or nonpreferred default audio. Provider labels are hints, not proof of English audio.", color = t.text)
                Text(selected.providerLabel, color = t.textMuted)
                ShButton(confirmLabel, { onChoose(selected.variantId, true) })
            } else {
                Text("Preferred: ${options.preferredLanguages.joinToString().ifEmpty { "All languages" }}", color = t.textMuted)
                LazyColumn(Modifier.weight(1f, fill = false), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(options.options, key = { it.variantId }) { option ->
                        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(option.providerLabel, color = t.textMuted, maxLines = 2)
                            val evidence = if (option.audioVerified) "Audio metadata: ${option.audioLanguages.joinToString()} · default ${option.defaultAudioLanguage ?: "unknown"}" else "Audio unverified · provider hint ${option.languageHint ?: "unknown"}"
                            ShButton("${option.sourceName} · $evidence", {
                                if (option.preferredAudioAvailable || options.preferredLanguages.isEmpty()) onChoose(option.variantId, false)
                                else pending = option
                            })
                        }
                    }
                }
            }
        }
    }
    LaunchedEffect(pending) { runCatching { cancelFocus.requestFocus() } }
}
