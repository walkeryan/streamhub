package com.streamhub.tv.core.ui.input

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.State
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.nativeKeyCode
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Digits typed on a screen that lists channels. Commits [ChannelNumberEntry.COMMIT_MS] after the last
 * digit, or at once on Select; Back discards what was typed. The player keeps the same state in its
 * view model (it must survive overlays); list screens use this.
 */
@Stable
class ChannelNumberState internal constructor(private val scope: CoroutineScope, private val onCommit: State<(Int) -> Unit>) {
    var entry by mutableStateOf("")
        private set
    private var pending: Job? = null
    /** Key whose release must not reach the focused row after its press committed or discarded the entry. */
    internal var swallowUp: Key? = null

    fun digit(d: Int) {
        entry = ChannelNumberEntry.append(entry, d)
        pending?.cancel()
        pending = scope.launch { delay(ChannelNumberEntry.COMMIT_MS); commit() }
    }

    fun commit(): Boolean {
        val n = ChannelNumberEntry.number(entry) ?: return false
        pending?.cancel(); entry = ""
        onCommit.value(n)
        return true
    }

    fun discard() { pending?.cancel(); entry = "" }
}

@Composable
fun rememberChannelNumberState(onCommit: (Int) -> Unit): ChannelNumberState {
    val scope = rememberCoroutineScope()
    val latest = rememberUpdatedState(onCommit)
    return remember { ChannelNumberState(scope, latest) }
}

/**
 * Put on the container of the channel rows (not on a text field's ancestor: digits there are text).
 * Digits type a number; while one is being typed Select commits it and Back discards it, and neither
 * reaches the focused row.
 */
fun Modifier.channelNumberKeys(state: ChannelNumberState): Modifier = onPreviewKeyEvent { e ->
    val digit = ChannelNumberEntry.digitOf(e.key.nativeKeyCode)
    val isSelect = e.key == Key.DirectionCenter || e.key == Key.Enter || e.key == Key.NumPadEnter
    when {
        digit != null -> { if (e.type == KeyEventType.KeyDown) state.digit(digit); true }
        e.type == KeyEventType.KeyUp && state.swallowUp == e.key -> { state.swallowUp = null; true }
        state.entry.isEmpty() -> false
        e.type != KeyEventType.KeyDown -> false
        isSelect -> { state.swallowUp = e.key; state.commit(); true }
        e.key == Key.Back -> { state.swallowUp = e.key; state.discard(); true }
        else -> false
    }
}

/**
 * The number being typed with, under it, the channel it names so far ([caption]). With an empty
 * [entry] only the caption shows, for the "No channel 105" answer. The caller places it (top-end,
 * inside the safe area).
 */
@Composable
fun ChannelNumberOverlay(entry: String, caption: String?, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    Column(
        modifier.background(t.overlay, RoundedCornerShape(Tokens.Radius.Md)).padding(20.dp),
        horizontalAlignment = Alignment.End,
    ) {
        if (entry.isNotEmpty()) {
            Text(entry, style = MaterialTheme.typography.displayMedium, color = t.text)
            Text(caption ?: "—", style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
        } else {
            Text(caption.orEmpty(), style = MaterialTheme.typography.bodyLarge, color = t.text)
        }
    }
}
