package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.unit.dp
import com.streamhub.tv.core.data.model.Row
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.Tokens

/**
 * Standard LazyRow (no TvLazy*) with:
 *  - Modifier.focusRestorer(): re-entering the row focuses the last-focused card (Back restores focus)
 *  - PivotBringIntoViewSpec: focused card sits at the safe margin
 *  - Left on the first card opens the rail
 */
@Composable
fun <T> FocusRow(
    items: List<T>,
    key: (T) -> Any,
    modifier: Modifier = Modifier,
    title: String? = null,
    state: LazyListState = rememberLazyListState(),
    contentPadding: PaddingValues = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX, vertical = 12.dp),
    item: @Composable (index: Int, item: T, itemModifier: Modifier) -> Unit,
) {
    val rail = LocalRailController.current
    Column(modifier.fillMaxWidth()) {
        if (title != null) {
            SectionTitle(title, Modifier.padding(start = Tokens.Canvas.SafeMarginX, bottom = 4.dp))
        }
        PivotScroll(leadingOffset = Tokens.Canvas.SafeMarginX) {
            LazyRow(
                state = state,
                modifier = Modifier.fillMaxWidth().focusRestorer(),
                contentPadding = contentPadding,
                horizontalArrangement = Arrangement.spacedBy(Tokens.Tile.Gap),
            ) {
                itemsIndexed(items, key = { _, it -> key(it) }) { index, it ->
                    item(index, it, if (index == 0) Modifier.leftOpensRail(rail) else Modifier)
                }
            }
        }
    }
}

/** API `Row` rendered with the tile type its layout asks for. */
@Composable
fun MediaRow(
    row: Row,
    onPlay: (Tile) -> Unit,
    onOpen: (Tile) -> Unit,
    onFocusTile: (Tile) -> Unit,
    modifier: Modifier = Modifier,
    state: LazyListState = rememberLazyListState(),
) {
    FocusRow(
        items = row.tiles,
        key = { it.id },
        title = row.title,
        state = state,
        modifier = modifier,
    ) { _, tile, m ->
        TileFor(
            tile = tile,
            layout = row.layout,
            // Channels and in-app episodes/continue-watching play directly; everything else opens details.
            onClick = { if (row.layout == com.streamhub.tv.core.data.model.RowLayout.CHANNEL || (tile.progressPct ?: 0.0) > 0 && tile.playableInApp) onPlay(tile) else onOpen(tile) },
            modifier = m,
            onFocus = onFocusTile,
        )
    }
}
