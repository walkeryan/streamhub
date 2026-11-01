package com.streamhub.tv.feature.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.theme.LocalThemeTokens

internal data class FilterOption(val value: String?, val label: String)

/** Ordinary Select opens a bounded, scrollable choice list; Back dismisses it. */
@Composable
internal fun CatalogFilterPicker(title: String, selected: String?, options: List<FilterOption>, onSelect: (String?) -> Unit, onDismiss: () -> Unit) {
    val initial = options.indexOfFirst { it.value == selected }.coerceAtLeast(0)
    val list = rememberLazyListState(initial)
    val focus = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        Column(Modifier.width(540.dp).heightIn(max = 480.dp).background(LocalThemeTokens.current.surface).padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title, style = MaterialTheme.typography.headlineSmall, color = LocalThemeTokens.current.text)
            Text("↑ ↓ Choose · Select Apply · Back Cancel", color = LocalThemeTokens.current.textMuted)
            LazyColumn(state = list, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                itemsIndexed(options, key = { i, _ -> i }) { index, option ->
                    ShChip(option.label, option.value == selected, { onSelect(option.value); onDismiss() }, Modifier.fillMaxWidth().then(if (index == initial) Modifier.focusRequester(focus) else Modifier))
                }
            }
        }
        LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    }
}
