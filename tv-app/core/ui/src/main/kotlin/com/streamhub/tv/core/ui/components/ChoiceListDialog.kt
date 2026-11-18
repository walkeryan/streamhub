package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
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
    Dialog(onDismissRequest = onDismiss) {
        ChoiceListCard(title, choices, selectedKey, onChoose, subtitle)
    }
}

/** The list's glass card: the mockup's menu rows, a tick on the choice in force. */
@Composable
internal fun ChoiceListCard(
    title: String,
    choices: List<ListChoice>,
    selectedKey: String?,
    onChoose: (String) -> Unit,
    subtitle: String? = null,
) {
    val initial = remember(choices, selectedKey) { initialChoiceIndex(choices.map { it.key to it.enabled }, selectedKey) }
    val list = rememberLazyListState(initial.coerceAtLeast(0))
    val focus = remember { FocusRequester() }
    val maxHeight = LocalConfiguration.current.screenHeightDp.dp - Tokens.Canvas.SafeMarginY * 2
    GlassPanel(
        Modifier.width(500.dp).heightIn(max = maxHeight),
        padding = PaddingValues(start = 22.dp, end = 22.dp, top = 26.dp, bottom = 18.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        DialogTitle(title, Modifier.padding(horizontal = 8.dp), maxLines = 1)
        if (subtitle != null) DialogText(subtitle, Modifier.padding(horizontal = 8.dp), maxLines = 2, fontSize = 16.sp)
        LazyColumn(
            state = list,
            modifier = Modifier.fillMaxWidth().weight(1f, fill = false).padding(top = 4.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
            // Room for the focus ring, which the list would otherwise clip.
            contentPadding = PaddingValues(4.dp),
        ) {
            itemsIndexed(choices, key = { _, c -> c.key }) { index, choice ->
                GlassRow(
                    label = choice.label,
                    onClick = { onChoose(choice.key) },
                    modifier = Modifier.fillMaxWidth().then(if (index == initial) Modifier.focusRequester(focus) else Modifier),
                    detail = choice.detail,
                    checkable = true,
                    checked = choice.key == selectedKey,
                    enabled = choice.enabled,
                )
            }
        }
    }
    LaunchedEffect(Unit) { withFrameNanos { }; runCatching { focus.requestFocus() } }
}

/**
 * Where focus starts in a list of (key, enabled) rows: the choice in force when it can be focused,
 * else the first row that can; -1 for a list with nothing to focus.
 */
fun initialChoiceIndex(rows: List<Pair<String, Boolean>>, selectedKey: String?): Int {
    val chosen = rows.indexOfFirst { it.first == selectedKey && it.second }
    return if (chosen >= 0) chosen else rows.indexOfFirst { it.second }
}
