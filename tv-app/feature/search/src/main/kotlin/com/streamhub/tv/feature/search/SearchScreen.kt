package com.streamhub.tv.feature.search

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.components.ChannelTile
import com.streamhub.tv.core.ui.components.FocusRow
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.PosterTile
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShTextField
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/**
 * Left: text field (voice via system keyboard mic) + recent searches. Right: Play here · Open in app · Channels.
 * The IME on Android TV is full-screen/bottom; results stay in the right pane so it never covers them
 * once dismissed (Back closes the IME and focus lands on the field; Right moves into results).
 */
@Composable
fun SearchScreen(vm: SearchViewModel = hiltViewModel()) {
    val t = LocalThemeTokens.current
    val q by vm.query.collectAsStateWithLifecycle()
    val ui by vm.ui.collectAsStateWithLifecycle()
    val recent by vm.recent.collectAsStateWithLifecycle()
    val launcher = LocalPlayLauncher.current
    val rail = LocalRailController.current
    val fieldFocus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { fieldFocus.requestFocus() } }

    Row(Modifier.fillMaxSize().padding(vertical = Tokens.Canvas.SafeMarginY)) {
        Column(Modifier.width(320.dp).fillMaxHeight().padding(start = Tokens.Canvas.SafeMarginX, end = 16.dp)) {
            ShTextField(
                value = q,
                onValueChange = vm::onQuery,
                label = "Search",
                placeholder = "Titles, channels, people",
                imeAction = ImeAction.Search,
                onImeAction = vm::commit,
                modifier = Modifier.fillMaxWidth().focusRequester(fieldFocus).leftOpensRail(rail),
            )
            if (recent.isNotEmpty()) {
                Text("Recent", style = MaterialTheme.typography.labelLarge, color = t.textMuted, modifier = Modifier.padding(top = 24.dp, bottom = 8.dp))
                LazyColumn(Modifier.focusRestorer(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(recent, key = { it }) { r ->
                        ShButton(r, onClick = { vm.onQuery(r) }, modifier = Modifier.leftOpensRail(rail))
                    }
                }
            }
        }
        Column(Modifier.weight(1f).fillMaxHeight()) {
            val res = ui.result
            when {
                ui.loading -> Text("Searching…", color = t.textMuted, style = MaterialTheme.typography.bodyLarge)
                ui.error != null -> Text("Search failed: ${ui.error}", color = t.danger, style = MaterialTheme.typography.bodyLarge)
                res == null -> Text(if (q.isBlank()) "Start typing to search all your sources." else "", color = t.textMuted, style = MaterialTheme.typography.bodyLarge)
                res.playHere.isEmpty() && res.openInApp.isEmpty() && res.channels.isEmpty() ->
                    Text("No results for “$q”.", color = t.textMuted, style = MaterialTheme.typography.bodyLarge)
                else -> PivotScroll(0.dp) {
                    LazyColumn(Modifier.fillMaxSize().focusRestorer(), contentPadding = PaddingValues(bottom = 48.dp)) {
                        if (res.playHere.isNotEmpty()) item("play") {
                            FocusRow(res.playHere, key = { it.id }, title = "Play here", contentPadding = PaddingValues(horizontal = 8.dp, vertical = 12.dp)) { _, tile, _ ->
                                PosterTile(tile, onClick = { vm.commit(); launcher.openDetails(tile.id) }, onLongClick = { launcher.play(tile.id) })
                            }
                        }
                        if (res.openInApp.isNotEmpty()) item("open") {
                            FocusRow(res.openInApp, key = { it.id }, title = "Open in app", contentPadding = PaddingValues(horizontal = 8.dp, vertical = 12.dp)) { _, tile, _ ->
                                PosterTile(tile, onClick = { vm.commit(); launcher.openDetails(tile.id) })
                            }
                        }
                        if (res.channels.isNotEmpty()) item("ch") {
                            FocusRow(res.channels, key = { it.id }, title = "Channels", contentPadding = PaddingValues(horizontal = 8.dp, vertical = 12.dp)) { _, ch, _ ->
                                ChannelTile(ch, onClick = { vm.commit(); launcher.play(ch.id) })
                            }
                        }
                        item("attr") {
                            Text(
                                res.attribution ?: "Availability data by JustWatch",
                                style = MaterialTheme.typography.labelMedium,
                                color = t.textMuted,
                                modifier = Modifier.padding(start = 8.dp, top = 16.dp).height(24.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

