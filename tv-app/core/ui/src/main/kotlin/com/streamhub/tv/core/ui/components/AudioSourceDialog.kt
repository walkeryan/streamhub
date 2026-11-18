package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.PlaybackOption
import com.streamhub.tv.core.data.model.PlaybackOptions
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.DialogProperties
import com.streamhub.tv.core.ui.theme.glass

/** Every action is reachable with four-way D-pad, Select and Back. Consent is per playback only. */
@Composable
fun AudioSourceDialog(options: PlaybackOptions, onDismiss: () -> Unit, confirmLabel:String="Play this source once", onChoose: (String, Boolean) -> Unit) {
    val g = glass
    var pending by remember(options) { mutableStateOf<PlaybackOption?>(null) }
    val cancelFocus = remember { FocusRequester() }
    Dialog(onDismissRequest = { if (pending != null) pending = null else onDismiss() }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        GlassPanel(Modifier.width(680.dp).heightIn(max = 486.dp), padding = PaddingValues(28.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            DialogTitle(if (pending == null) "Choose audio / source" else "Confirm audio choice", maxLines = 1)
            ShButton(if (pending == null) "Cancel" else "Back to sources", { if (pending != null) pending = null else onDismiss() }, Modifier.focusRequester(cancelFocus))
            val selected = pending
            if (selected != null) {
                DialogText("This source has unverified or nonpreferred default audio. Provider labels are hints, not proof of English audio.", color = g.text)
                DialogText(selected.providerLabel)
                ShButton(confirmLabel, { onChoose(selected.variantId, true) })
            } else {
                DialogText("Preferred: ${options.preferredLanguages.joinToString().ifEmpty { "All languages" }}", fontSize = 16.sp)
                LazyColumn(Modifier.weight(1f, fill = false), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(6.dp)) {
                    items(options.options, key = { it.variantId }) { option ->
                        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(option.providerLabel, color = g.muted, fontSize = 15.sp, maxLines = 2)
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
