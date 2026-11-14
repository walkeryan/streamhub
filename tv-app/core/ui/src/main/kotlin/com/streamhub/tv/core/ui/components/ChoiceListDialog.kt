package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** One row of a [ChoiceListDialog]: [detail] sits at the row's end (a count, a channel number). */
data class ListChoice(val key: String, val label: String, val detail: String? = null, val enabled: Boolean = true)

/**
 * A short list for the D-pad, in its own focus window: Up and Down walk it, Select chooses and
 * closes, Back closes and changes nothing. Focus starts on the choice in force (else on the first
 * row), and the list scrolls inside the safe area when it is longer than the screen. Used wherever
 * a row of chips would grow too long to walk: a channel group's children, a series' seasons, the
 * audio tracks of a film, the channels carrying a game.
 */
@Composable
fun ChoiceListDialog(
    title: String,
    choices: List<ListChoice>,
    selectedKey: String?,
    onChoose: (String) -> Unit,
    onDismiss: () -> Unit,
    subtitle: String? = null,
) {
    val t = LocalThemeTokens.current
    val initial = remember(choices, selectedKey) { initialChoiceIndex(choices.map { it.key to it.enabled }, selectedKey) }
    val list = rememberLazyListState(initial.coerceAtLeast(0))
    val focus = remember { FocusRequester() }
    val maxHeight = LocalConfiguration.current.screenHeightDp.dp - Tokens.Canvas.SafeMarginY * 2
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.width(480.dp).heightIn(max = maxHeight).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleLarge, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = t.textMuted, maxLines = 2, overflow = TextOverflow.Ellipsis)
            LazyColumn(
                state = list,
                modifier = Modifier.fillMaxWidth().weight(1f, fill = false),
                verticalArrangement = Arrangement.spacedBy(6.dp),
                // Room for the focus ring, which the list would otherwise clip.
                contentPadding = PaddingValues(4.dp),
            ) {
                itemsIndexed(choices, key = { _, c -> c.key }) { index, choice ->
                    val chosen = choice.key == selectedKey
                    FocusSurface(
                        onClick = { onChoose(choice.key) },
                        modifier = Modifier.fillMaxWidth().height(48.dp).then(if (index == initial) Modifier.focusRequester(focus) else Modifier),
                        shape = RoundedCornerShape(Tokens.Radius.Md),
                        containerColor = if (chosen) t.surfaceAlt else t.surface,
                        focusedContainerColor = t.text,
                        scaleOnFocus = false,
                        glow = false,
                        enabled = choice.enabled,
                    ) { focused ->
                        val ink = (if (focused) t.bg else t.text).let { if (choice.enabled) it else it.copy(alpha = 0.6f) }
                        Row(Modifier.fillMaxSize().padding(horizontal = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                            // The mark is text, so the choice in force is never told by colour alone.
                            Text(if (chosen) "✓" else "", color = ink, style = MaterialTheme.typography.labelLarge, modifier = Modifier.width(22.dp))
                            Text(choice.label, Modifier.weight(1f), color = ink, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            if (choice.detail != null) Text(choice.detail, color = if (focused) t.bg else t.textMuted, style = MaterialTheme.typography.labelMedium, maxLines = 1, modifier = Modifier.padding(start = 12.dp))
                        }
                    }
                }
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { focus.requestFocus() } }
    }
}

/**
 * Where focus starts in a list of (key, enabled) rows: the choice in force when it can be focused,
 * else the first row that can; -1 for a list with nothing to focus.
 */
fun initialChoiceIndex(rows: List<Pair<String, Boolean>>, selectedKey: String?): Int {
    val chosen = rows.indexOfFirst { it.first == selectedKey && it.second }
    return if (chosen >= 0) chosen else rows.indexOfFirst { it.second }
}
