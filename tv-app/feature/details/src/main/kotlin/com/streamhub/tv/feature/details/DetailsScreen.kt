package com.streamhub.tv.feature.details

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.PlaylistAdd
import androidx.compose.material.icons.filled.PlaylistAddCheck
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.window.Dialog
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.ui.components.WatchTogetherDialog
import com.streamhub.tv.core.data.model.RequestStates
import com.streamhub.tv.core.ui.components.ErrorDialog
import com.streamhub.tv.core.data.repo.isOpenRequest
import com.streamhub.tv.core.data.repo.requestPollMs
import com.streamhub.tv.core.data.repo.requestStatus
import com.streamhub.tv.core.data.repo.requestTarget
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.SourceBadge
import com.streamhub.tv.core.data.model.TitleDetail
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.CenterMessage
import com.streamhub.tv.core.ui.components.FocusRow
import com.streamhub.tv.core.ui.components.HeroBackdrop
import com.streamhub.tv.core.ui.components.HeroIcons
import com.streamhub.tv.core.ui.components.LandscapeTile
import com.streamhub.tv.core.ui.components.LoadingView
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.RequestStatus
import com.streamhub.tv.core.ui.components.WhileVisible
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.components.metaLine
import com.streamhub.tv.core.ui.components.shortLabel
import com.streamhub.tv.core.ui.focus.InitialFocusOnce
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.delay

/**
 * Title detail: backdrop+scrim, poster left, meta right, action row
 * (Play/Resume · source picker · Watchlist · Favorite · Trailer · Mark watched), seasons/episodes,
 * "Available on" (in-app first, then hand-off) with JustWatch attribution.
 */
@Composable
fun DetailsScreen(vm: DetailsViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    when {
        s.loading -> LoadingView()
        s.error != null -> CenterMessage("Couldn't load this title", s.error) { ShButton("Retry", vm::load, style = ButtonStyle.Primary) }
        s.detail != null -> DetailsContent(s, vm)
    }
}

private fun resumeLabel(d: TitleDetail): String? {
    val pct = d.progressPct ?: return null
    if (pct <= 0 || pct >= 97) return null
    val rt = d.runtimeMin ?: return "Resume"
    val left = (rt * (1 - pct / 100.0)).toInt().coerceAtLeast(1)
    return "Resume · $left min left"
}

@Composable
private fun DetailsContent(s: DetailsState, vm: DetailsViewModel) {
    val d = s.detail!!
    val t = LocalThemeTokens.current
    val launcher = LocalPlayLauncher.current
    val rail = LocalRailController.current
    val playFocus = remember { FocusRequester() }
    val inApp = d.sources.filter { it.playableInApp }
    val handoff = d.sources.filterNot { it.playableInApp }
    var chosenSource by remember(d.id) { mutableStateOf(inApp.firstOrNull()) }
    val listFocus = remember { FocusRequester() }
    // Request: offered for a title the server marks requestable, and kept in place (showing where the
    // request stands) once one is open, so the button under the viewer's focus does not vanish.
    val requestOpen = isOpenRequest(s.requestState)
    // One status for the request, in place of a sentence: the chip of its phase and, while it downloads, the bar.
    val requestStatus = s.requestProgress?.takeIf { s.requestState != null }?.let { progress -> requestStatus(progress) }
    // Followed only while this page is in front: every 15 s while it downloads or imports, else every 3 min.
    val pollMs = requestPollMs(listOf(s.requestProgress?.phase))
    if (s.requestState != null && (requestOpen || (s.requestState == RequestStates.AVAILABLE && inApp.isEmpty()))) {
        WhileVisible(pollMs to d.id) { while (true) { delay(pollMs); vm.pollRequest() } }
    }
    val showRequest = s.canRequest && requestTarget(d.id) != null && (d.requestable || requestOpen)
    // With nothing to play here, Request is the first thing this screen is for.
    val requestFirst = showRequest && inApp.isEmpty()
    // Play on first show of this title only; back from the player (or a related title) restores focus.
    InitialFocusOnce(initial = playFocus, restore = listFocus, key = d.id)
    LaunchedEffect(s.toast) { if (s.toast != null) { delay(2500); vm.clearToast() } }

    if (s.seasonPicker) SeasonPickerDialog(d.title, d.requestSeasons, onAll = { vm.submitRequest(null) }, onPicked = vm::submitRequest, onDismiss = vm::closeSeasonPicker)
    s.requestError?.let { ErrorDialog(it, onRetry = { vm.dismissRequestError(); vm.request() }, onDismiss = vm::dismissRequestError) }
    val together by vm.together.state.collectAsStateWithLifecycle()
    together?.let { WatchTogetherDialog(it, onStart = vm.together::start, onRetry = vm.together::retry, onCancel = vm.together::cancel) }
    // Hold Select on an episode: what else can be done with it besides playing it here.
    var episodeActions by remember(d.id) { mutableStateOf<Tile?>(null) }
    episodeActions?.let { ep ->
        EpisodeDialog(
            ep,
            onPlay = { episodeActions = null; launcher.play(ep.id) },
            onTogether = { episodeActions = null; vm.watchEpisodeTogether(ep) },
            onDismiss = { episodeActions = null },
        )
    }
    Box(Modifier.fillMaxSize()) {
        HeroBackdrop(d.asTile())
        PivotScroll(0.dp) {
            LazyColumn(Modifier.fillMaxSize().focusRequester(listFocus).focusRestorer(), contentPadding = PaddingValues(vertical = Tokens.Canvas.SafeMarginY)) {
                item("head") {
                    Row(Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = 24.dp)) {
                        AsyncImage(
                            d.poster, d.title, contentScale = ContentScale.Crop,
                            modifier = Modifier.size(Tokens.Tile.PosterW * 1.2f, Tokens.Tile.PosterH * 1.2f).clip(RoundedCornerShape(Tokens.Radius.Md)),
                        )
                        Column(Modifier.padding(start = 32.dp).width(600.dp)) {
                            Text(d.title, style = MaterialTheme.typography.displaySmall, color = t.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            Text(
                                listOfNotNull(d.asTile().metaLine().ifBlank { null }, d.runtimeMin?.let { "$it min" }, d.genres.take(3).joinToString(", ").ifBlank { null }).joinToString("  ·  "),
                                style = MaterialTheme.typography.bodyLarge, color = t.textMuted, modifier = Modifier.padding(top = 8.dp),
                            )
                            d.overview?.let { Text(it, style = MaterialTheme.typography.bodyLarge, color = t.text, maxLines = 4, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 12.dp)) }
                            if (d.cast.isNotEmpty()) {
                                Text("Cast: " + d.cast.take(5).joinToString(", ") { it.name }, style = MaterialTheme.typography.bodyMedium, color = t.textMuted, maxLines = 1, modifier = Modifier.padding(top = 8.dp))
                            }
                        }
                    }
                }
                if (d.sources.isEmpty()) item("unavailable") { Text("Not available from your sources", color = t.textMuted, modifier = Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX)) }
                item("actions") {
                    LazyRow(
                        Modifier.fillMaxWidth().focusRestorer(),
                        contentPadding = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX, vertical = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        if (showRequest) item("request") {
                            ShButton(
                                // While a request is open the button names its phase; Select then asks how it stands now.
                                when { s.requesting -> "Requesting…"; requestOpen -> requestStatus?.label ?: "Requested"; else -> "Request" },
                                onClick = { if (requestOpen) vm.checkRequest() else vm.request() },
                                icon = if (requestOpen) Icons.Filled.Check else Icons.Filled.Add,
                                style = if (requestFirst && !requestOpen) ButtonStyle.Primary else ButtonStyle.Secondary,
                                modifier = if (requestFirst) Modifier.focusRequester(playFocus).leftOpensRail(rail) else Modifier,
                            )
                        }
                        if (d.sources.isNotEmpty()) item("play") {
                            val label = if (inApp.isNotEmpty()) "Choose audio / source" else "Open in ${handoff.firstOrNull()?.name ?: "app"}"
                            ShButton(
                                label,
                                onClick = { launcher.play(d.id, sourceId = chosenSource?.sourceId ?: handoff.firstOrNull()?.sourceId) },
                                icon = if (inApp.isNotEmpty()) HeroIcons.Play else HeroIcons.Open,
                                style = if (requestFirst) ButtonStyle.Secondary else ButtonStyle.Primary,
                                modifier = if (requestFirst) Modifier else Modifier.focusRequester(playFocus).leftOpensRail(rail),
                            )
                        }
                        // A film or an episode as it is; a series opens the room on the next episode to watch.
                        if (s.canWatchTogether && inApp.isNotEmpty()) item("together") {
                            ShButton("Watch together", vm::watchTogether, icon = Icons.Filled.Group)
                        }
                        if (inApp.size > 1) {
                            items(inApp, key = { "src-${it.sourceId}" }) { b ->
                                ShChip("${b.shortLabel()}${b.quality?.let { " $it" } ?: ""}", chosenSource == b, { chosenSource = b })
                            }
                        }
                        item("wl") { ShButton(if (s.inWatchlist) "In watchlist" else "Watchlist", vm::toggleWatchlist, modifier = if (d.sources.isEmpty() && !requestFirst) Modifier.focusRequester(playFocus) else Modifier, icon = if (s.inWatchlist) Icons.Filled.PlaylistAddCheck else Icons.Filled.PlaylistAdd) }
                        item("fav") { ShButton(if (d.isFavorite) "Favorited" else "Favorite", vm::toggleFavorite, icon = if (d.isFavorite) Icons.Filled.Favorite else Icons.Filled.FavoriteBorder) }
                        item("trailer") {
                            // Trailer is resolved server-side to a YouTube hand-off descriptor.
                            ShButton("Trailer", { launcher.play("trailer:${d.id}") }, icon = Icons.Filled.Movie)
                        }
                        item("watched") { ShButton("Mark watched", vm::markWatched, icon = Icons.Filled.Check) }
                    }
                    // Where the request stands, while that is news: once the title is simply available it is not.
                    // The toast line is for passing messages.
                    requestStatus?.takeIf { s.requestState != RequestStates.AVAILABLE || inApp.isEmpty() }?.let {
                        RequestStatus(it, Modifier.padding(start = Tokens.Canvas.SafeMarginX, end = Tokens.Canvas.SafeMarginX, top = 6.dp))
                    }
                    s.toast?.let { Text(it, color = t.accent, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX, top = 4.dp)) }
                }
                if (d.seasons.isNotEmpty()) item("seasons") {
                    FocusRow(d.seasons, key = { it.id }, title = "Seasons") { _, season, m ->
                        ShChip(season.title, s.selectedSeason == season.id, { vm.selectSeason(season.id) }, m)
                    }
                }
                if (s.seasonEpisodes.isNotEmpty()) item("episodes") {
                    FocusRow(s.seasonEpisodes, key = { it.id }, title = "Episodes") { _, ep, m ->
                        LandscapeTile(ep, onClick = { launcher.play(ep.id) }, modifier = m, onLongClick = if (s.canWatchTogether) ({ episodeActions = ep }) else null)
                    }
                    if (s.canWatchTogether) Text(
                        "Select plays an episode · hold Select to watch it together",
                        style = MaterialTheme.typography.labelMedium, color = t.textMuted,
                        modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX, top = 4.dp),
                    )
                }
                if (d.sources.isNotEmpty()) item("available") {
                    AvailableOn(inApp + handoff, d.attribution, onClick = { b -> launcher.play(d.id, sourceId = b.sourceId) })
                }
            }
        }
    }
}

/** A series request: everything, or the seasons ticked here. */
@Composable
private fun SeasonPickerDialog(title: String, seasons: List<Int>, onAll: () -> Unit, onPicked: (List<Int>) -> Unit, onDismiss: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    var picked by remember { mutableStateOf(emptySet<Int>()) }
    Dialog(onDismissRequest = onDismiss) {
        Column(Modifier.width(560.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(28.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Request $title", style = MaterialTheme.typography.titleLarge, color = t.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            ShButton("All seasons", onAll, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
            Text("Or pick seasons:", style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
            LazyRow(Modifier.fillMaxWidth().focusRestorer(), horizontalArrangement = Arrangement.spacedBy(8.dp), contentPadding = PaddingValues(vertical = 4.dp, horizontal = 4.dp)) {
                items(seasons.sorted(), key = { it }) { n ->
                    ShChip(if (n == 0) "Specials" else "Season $n", n in picked, { picked = if (n in picked) picked - n else picked + n })
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                // Never disabled (focus would be stranded on it): with nothing ticked it says what to do.
                ShButton(
                    when (picked.size) { 0 -> "Tick seasons above"; 1 -> "Request 1 season"; else -> "Request ${picked.size} seasons" },
                    { if (picked.isNotEmpty()) onPicked(picked.sorted()) },
                )
                ShButton("Cancel", onDismiss)
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { focus.requestFocus() } }
    }
}

/** An episode's own actions: play it here, or open a watch room on it. Focus starts on Play. */
@Composable
private fun EpisodeDialog(episode: Tile, onPlay: () -> Unit, onTogether: () -> Unit, onDismiss: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        Column(Modifier.width(520.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(28.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(episode.title, style = MaterialTheme.typography.titleLarge, color = t.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            episode.subtitle?.takeIf(String::isNotBlank)?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis) }
            ShButton("Play", onPlay, Modifier.fillMaxWidth().focusRequester(focus), icon = HeroIcons.Play, style = ButtonStyle.Primary)
            ShButton("Watch together", onTogether, Modifier.fillMaxWidth(), icon = Icons.Filled.Group)
            ShButton("Close", onDismiss, Modifier.fillMaxWidth())
        }
        LaunchedEffect(episode.id) { withFrameNanos { }; runCatching { focus.requestFocus() } }
    }
}

@Composable
private fun AvailableOn(sources: List<SourceBadge>, attribution: String?, onClick: (SourceBadge) -> Unit) {
    val t = LocalThemeTokens.current
    Column(Modifier.padding(top = 16.dp)) {
        FocusRow(sources, key = { "${it.sourceId}-${it.kind}" }, title = "Available on") { _, b, m ->
            ShButton(
                (if (b.playableInApp) "▶ " else "↗ ") + b.name + (b.kind?.let { " · $it" } ?: ""),
                { onClick(b) },
                modifier = m,
            )
        }
        Text(
            attribution ?: "Availability data by JustWatch",
            style = MaterialTheme.typography.labelMedium, color = t.textMuted,
            modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX, top = 4.dp),
        )
    }
}
