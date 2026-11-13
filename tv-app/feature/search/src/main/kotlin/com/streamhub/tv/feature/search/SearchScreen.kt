package com.streamhub.tv.feature.search

import androidx.compose.foundation.background
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.RecordingStates
import com.streamhub.tv.core.data.model.SearchProgramme
import com.streamhub.tv.core.data.repo.recordingStateMark
import com.streamhub.tv.core.ui.components.ChannelTile
import com.streamhub.tv.core.ui.components.ErrorDialog
import com.streamhub.tv.core.ui.components.FocusRow
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.PosterTile
import com.streamhub.tv.core.ui.components.ProgrammeDialog
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShTextField
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.InitialFocusOnce
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId

/** A row's entries: its results, and "Show more" at the end while the server holds more of them. */
private sealed interface RowEntry<out T> {
    data class Item<T>(val value: T, val key: String) : RowEntry<T>
    data object More : RowEntry<Nothing>
}

/**
 * One field and, for what it finds, three rows: On TV (guide broadcasts on now or starting within
 * 48 hours), Channels, and Movies & Shows, in the order the server gives (Channels first for a
 * channel's number or whole name). The hub's own results are shown as soon as they land; the titles
 * the metadata provider adds join the end of Movies & Shows afterwards, and nothing that is on
 * screen moves when they do.
 *
 * Left: the field (voice via the system keyboard's mic) and recent searches. Right: the rows. The
 * IME on Android TV is full-screen/bottom; results stay in the right pane so it never covers them
 * once dismissed (Back closes the IME and focus lands on the field; Right moves into results).
 * Entering the screen focuses the results when a search is already there, otherwise the field.
 */
@Composable
fun SearchScreen(vm: SearchViewModel = hiltViewModel()) {
    val t = LocalThemeTokens.current
    val q by vm.query.collectAsStateWithLifecycle()
    val ui by vm.ui.collectAsStateWithLifecycle()
    val recent by vm.recent.collectAsStateWithLifecycle()
    val recorded by vm.recorded.collectAsStateWithLifecycle()
    val recording by vm.recorder.availability.collectAsStateWithLifecycle()
    val record by vm.recorder.state.collectAsStateWithLifecycle()
    val launcher = LocalPlayLauncher.current
    val rail = LocalRailController.current
    val resultsFocus = remember { FocusRequester() }
    val fieldFocus = remember { FocusRequester() }
    val screenFocus = remember { FocusRequester() }
    val scope = rememberCoroutineScope()
    val zone = remember { ZoneId.systemDefault() }
    val view = ui.view
    val hasResults = (view?.found ?: 0) > 0
    // First show: the results if a search is already on screen, otherwise the field. After Back from
    // Details or the player the screen-level focusRestorer re-enters the results column, whose
    // restorer (and each row's) lands on the tile that was opened.
    InitialFocusOnce(initial = if (hasResults) resultsFocus else fieldFocus, restore = screenFocus)

    // A broadcast still to come: its dialog, and the card focus returns to.
    var broadcast by remember { mutableStateOf<Pair<SearchProgramme, FocusRequester>?>(null) }
    // "Show more" was pressed on this row when it had this many entries: focus goes to the first one added.
    var grown by remember { mutableStateOf<Pair<SearchSectionName, Int>?>(null) }
    val grownFocus = remember { FocusRequester() }
    // A failed fetch of more rows leaves the row as it was, "Show more" included.
    LaunchedEffect(ui.growing, ui.loading) { if (!ui.growing && !ui.loading) grown?.let { (name, from) -> if ((view?.sizeOf(name) ?: 0) <= from) grown = null } }

    Row(
        Modifier
            .fillMaxSize()
            .padding(vertical = Tokens.Canvas.SafeMarginY)
            .focusRequester(screenFocus)
            .focusRestorer()
            .focusGroup(),
    ) {
        Column(Modifier.width(300.dp).fillMaxHeight().padding(start = Tokens.Canvas.SafeMarginX, end = 16.dp)) {
            ShTextField(
                value = q,
                onValueChange = vm::onQuery,
                label = "Search",
                placeholder = "Movies, shows, channels, what's on",
                imeAction = ImeAction.Search,
                onImeAction = vm::commit,
                modifier = Modifier.fillMaxWidth().focusRequester(fieldFocus).leftOpensRail(rail),
            )
            if (recent.isNotEmpty()) {
                Text("Recent", style = MaterialTheme.typography.labelLarge, color = t.textMuted, modifier = Modifier.padding(top = 24.dp, bottom = 8.dp))
                LazyColumn(Modifier.focusRestorer(), verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = PaddingValues(4.dp)) {
                    items(recent, key = { it }) { r ->
                        ShButton(r, onClick = { vm.onQuery(r) }, modifier = Modifier.leftOpensRail(rail))
                    }
                }
            }
        }
        Column(Modifier.weight(1f).fillMaxHeight()) {
            val quiet = Modifier.padding(start = 8.dp, end = Tokens.Canvas.SafeMarginX)
            when {
                q.trim().length < 2 -> Text("Movies, shows, channels and what is on TV.", quiet, color = t.textMuted, style = MaterialTheme.typography.bodyLarge)
                view == null && ui.error != null -> Text("Search failed: ${ui.error}", quiet, color = t.danger, style = MaterialTheme.typography.bodyLarge)
                // Nothing to show yet, or the hub holds nothing and the provider has not answered: not "No results" too soon.
                view == null || (view.found == 0 && (ui.loading || ui.discovering)) -> Text("Searching…", quiet, color = t.textMuted, style = MaterialTheme.typography.bodyLarge)
                view.found == 0 -> Text("No results for “${q.trim()}”. Check the spelling, or try fewer words. Channels are also found by number.", quiet, color = t.textMuted, style = MaterialTheme.typography.bodyLarge)
                else -> PivotScroll(0.dp) {
                    LazyColumn(Modifier.fillMaxSize().focusRequester(resultsFocus).focusRestorer(), contentPadding = PaddingValues(bottom = 48.dp)) {
                        items(view.order.filter { view.sizeOf(it) > 0 }, key = { it.wire }) { name ->
                            val more = name in view.more
                            when (name) {
                                SearchSectionName.PROGRAMMES -> ResultRow(name, view.programmes.map { RowEntry.Item(it, it.id) }, more, ui.growing, grown, grownFocus, { grown = null }, { grown = name to view.programmes.size; vm.showMore(name) },
                                    Tokens.Tile.LandscapeW, BROADCAST_H) { p, modifier ->
                                    val now = Instant.now()
                                    val opener = remember(p.id) { FocusRequester() }
                                    BroadcastCard(p, broadcastOnNow(p, now), broadcastStart(p.start, now, zone), recorded[p.id] ?: p.recordingState, modifier.focusRequester(opener)) {
                                        vm.commit()
                                        if (broadcastOnNow(p, Instant.now())) launcher.play(p.channelId)
                                        else { vm.recorder.reset(); broadcast = p to opener }
                                    }
                                }
                                SearchSectionName.CHANNELS -> ResultRow(name, view.channels.map { RowEntry.Item(it, it.id) }, more, ui.growing, grown, grownFocus, { grown = null }, { grown = name to view.channels.size; vm.showMore(name) },
                                    Tokens.Tile.ChannelW, Tokens.Tile.ChannelH) { ch, modifier ->
                                    ChannelTile(ch, onClick = { vm.commit(); launcher.play(ch.id) }, modifier = modifier)
                                }
                                SearchSectionName.TITLES -> ResultRow(name, view.titles.map { RowEntry.Item(it, it.id) }, more, ui.growing, grown, grownFocus, { grown = null }, { grown = name to view.titles.size; vm.showMore(name) },
                                    Tokens.Tile.PosterW, Tokens.Tile.PosterH) { tile, modifier ->
                                    // A tick on what plays here; where a request is in progress, how it stands; otherwise nothing.
                                    PosterTile(tile, onClick = { vm.commit(); launcher.openDetails(tile.id) }, modifier = modifier, badge = titleTick(tile), showSources = false)
                                }
                            }
                        }
                        // More titles may still be on their way: said where they will go.
                        if (ui.discovering) item("discovering") { Text("Looking for more titles…", quiet.padding(top = 4.dp), color = t.textMuted, style = MaterialTheme.typography.labelMedium) }
                        if (view.titles.isNotEmpty()) item("attr") {
                            Text(
                                view.attribution ?: "Availability data by JustWatch",
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

    broadcast?.let { (p, opener) ->
        val close = { broadcast = null; vm.recorder.reset(); scope.launch { withFrameNanos { }; runCatching { opener.requestFocus() } }; Unit }
        val now = Instant.now()
        val onAir = broadcastOnNow(p, now)
        val state = recorded[p.id] ?: p.recordingState
        val subtitle = listOf(broadcastChannelLine(p), if (onAir) "On now" else broadcastStart(p.start, now, zone)).filter { it.isNotBlank() }.joinToString(" · ")
        if (recording.canSchedule) ProgrammeDialog(
            programme = p.asProgramme().copy(recordingState = state),
            subtitle = subtitle,
            onWatch = if (onAir) ({ close(); launcher.play(p.channelId) }) else null,
            canRecordThis = state != RecordingStates.SCHEDULED && state != RecordingStates.RECORDING,
            busy = record.busy,
            outcome = record.outcome,
            onRecord = { vm.recorder.recordOnce(p.asProgramme()) },
            onRecordSeries = { vm.recorder.recordSeries(p.asProgramme()) },
            onRecordTeam = vm.recorder::recordTeam,
            onDismiss = close,
        )
        // No recorder, or no permission to schedule: when and where it is on, and a way back.
        else ErrorDialog(UserError(p.title, subtitle, canRetry = false), onRetry = {}, onDismiss = close, dismissLabel = "Close")
    }
}

private fun SearchView.sizeOf(name: SearchSectionName): Int = when (name) {
    SearchSectionName.PROGRAMMES -> programmes.size
    SearchSectionName.CHANNELS -> channels.size
    SearchSectionName.TITLES -> titles.size
}

private val BROADCAST_H = 104.dp

/**
 * One result row. Its entries are keyed, so rows added at the end leave the focused card where it
 * is. "Show more" is the last card while the server holds more; when the rest arrives focus moves to
 * the first card that was added, which takes the place "Show more" had.
 */
@Composable
private fun <T> ResultRow(
    name: SearchSectionName,
    items: List<RowEntry.Item<T>>,
    more: Boolean,
    growing: Boolean,
    grown: Pair<SearchSectionName, Int>?,
    grownFocus: FocusRequester,
    onGrown: () -> Unit,
    onMore: () -> Unit,
    cardWidth: Dp,
    cardHeight: Dp,
    card: @Composable (T, Modifier) -> Unit,
) {
    val state = rememberLazyListState()
    val entries: List<RowEntry<T>> = if (more) items + RowEntry.More else items
    val from = grown?.takeIf { it.first == name }?.second
    LaunchedEffect(from, items.size) {
        if (from != null && items.size > from) {
            runCatching { state.scrollToItem(from) }
            withFrameNanos { }
            runCatching { grownFocus.requestFocus() }
            onGrown()
        }
    }
    FocusRow(entries, key = { if (it is RowEntry.Item<*>) it.key else "more" }, title = name.heading, state = state, contentPadding = PaddingValues(start = 8.dp, end = Tokens.Canvas.SafeMarginX, top = 12.dp, bottom = 12.dp)) { index, entry, modifier ->
        when (entry) {
            is RowEntry.Item -> card(entry.value, if (index == from) modifier.focusRequester(grownFocus) else modifier)
            RowEntry.More -> MoreCard(growing, cardWidth.coerceAtMost(Tokens.Tile.PosterW), cardHeight, modifier, onMore)
        }
    }
}

@Composable
private fun MoreCard(growing: Boolean, width: Dp, height: Dp, modifier: Modifier, onClick: () -> Unit) {
    val t = LocalThemeTokens.current
    FocusSurface(onClick = { if (!growing) onClick() }, modifier = modifier.size(width, height), containerColor = t.surfaceAlt) {
        Text(if (growing) "Loading…" else "Show more", color = t.text, style = MaterialTheme.typography.labelLarge, modifier = Modifier.align(Alignment.Center))
    }
}

/** One broadcast: "On now" or the day and time it starts, its title, and where it is on. */
@Composable
private fun BroadcastCard(p: SearchProgramme, live: Boolean, starts: String, recordingState: String?, modifier: Modifier, onClick: () -> Unit) {
    val t = LocalThemeTokens.current
    FocusSurface(onClick = onClick, modifier = modifier.size(Tokens.Tile.LandscapeW, BROADCAST_H), containerColor = t.surfaceAlt) {
        Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                if (live) Text("On now", color = t.accentText, style = MaterialTheme.typography.labelSmall, maxLines = 1,
                    modifier = Modifier.background(t.accent, RoundedCornerShape(4.dp)).padding(horizontal = 6.dp, vertical = 2.dp))
                else Text(starts, color = t.accent, style = MaterialTheme.typography.labelLarge, maxLines = 1)
                recordingStateMark(recordingState)?.let { Text(it, color = t.accent, style = MaterialTheme.typography.labelLarge) }
            }
            Text(p.title, color = t.text, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text(broadcastChannelLine(p), color = t.textMuted, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}
