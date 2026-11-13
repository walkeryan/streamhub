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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.room.RoomCode
import com.streamhub.tv.core.ui.components.QrCode
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
    // Play on first show of this title only; back from the player (or a related title) restores focus.
    InitialFocusOnce(initial = playFocus, restore = listFocus, key = d.id)
    LaunchedEffect(s.toast) { if (s.toast != null) { delay(2500); vm.clearToast() } }

    s.together?.let { WatchTogetherDialog(it, d.title, onStart = vm::startRoom, onRetry = vm::watchTogether, onCancel = vm::cancelRoom) }
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
                        if (d.sources.isNotEmpty()) item("play") {
                            val label = if (inApp.isNotEmpty()) "Choose audio / source" else "Open in ${handoff.firstOrNull()?.name ?: "app"}"
                            ShButton(
                                label,
                                onClick = { launcher.play(d.id, sourceId = chosenSource?.sourceId ?: handoff.firstOrNull()?.sourceId) },
                                icon = if (inApp.isNotEmpty()) HeroIcons.Play else HeroIcons.Open,
                                style = ButtonStyle.Primary,
                                modifier = Modifier.focusRequester(playFocus).leftOpensRail(rail),
                            )
                        }
                        // A room plays one title for everyone: films and episodes, not a whole series.
                        if (s.canWatchTogether && inApp.isNotEmpty() && d.kind !in SERIES_KINDS) item("together") {
                            ShButton("Watch together", vm::watchTogether, icon = Icons.Filled.Group)
                        }
                        if (inApp.size > 1) {
                            items(inApp, key = { "src-${it.sourceId}" }) { b ->
                                ShChip("${b.shortLabel()}${b.quality?.let { " $it" } ?: ""}", chosenSource == b, { chosenSource = b })
                            }
                        }
                        item("wl") { ShButton(if (s.inWatchlist) "In watchlist" else "Watchlist", vm::toggleWatchlist, modifier = if (d.sources.isEmpty()) Modifier.focusRequester(playFocus) else Modifier, icon = if (s.inWatchlist) Icons.Filled.PlaylistAddCheck else Icons.Filled.PlaylistAdd) }
                        item("fav") { ShButton(if (d.isFavorite) "Favorited" else "Favorite", vm::toggleFavorite, icon = if (d.isFavorite) Icons.Filled.Favorite else Icons.Filled.FavoriteBorder) }
                        item("trailer") {
                            // Trailer is resolved server-side to a YouTube hand-off descriptor.
                            ShButton("Trailer", { launcher.play("trailer:${d.id}") }, icon = Icons.Filled.Movie)
                        }
                        item("watched") { ShButton("Mark watched", vm::markWatched, icon = Icons.Filled.Check) }
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
                        LandscapeTile(ep, onClick = { launcher.play(ep.id) }, modifier = m)
                    }
                }
                if (d.sources.isNotEmpty()) item("available") {
                    AvailableOn(inApp + handoff, d.attribution, onClick = { b -> launcher.play(d.id, sourceId = b.sourceId) })
                }
            }
        }
    }
}

private val SERIES_KINDS = setOf(MediaKind.SHOW, MediaKind.SERIES, MediaKind.SEASON)

/**
 * The room's code, large enough to read across the room, and a QR code of `<server>/room/<code>` for
 * phones and computers. Start joins the room and opens the player; Back or Cancel closes the room.
 */
@Composable
private fun WatchTogetherDialog(state: WatchTogether, title: String, onStart: () -> Unit, onRetry: () -> Unit, onCancel: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    Dialog(onDismissRequest = onCancel, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Row(
            Modifier.width(820.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(32.dp),
            horizontalArrangement = Arrangement.spacedBy(32.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Watch together", style = MaterialTheme.typography.titleLarge, color = t.text)
                Text(title, style = MaterialTheme.typography.bodyLarge, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                when (state) {
                    WatchTogether.Creating -> {
                        Text("Opening a room…", style = MaterialTheme.typography.bodyLarge, color = t.text)
                        ShButton("Cancel", onCancel, Modifier.focusRequester(focus))
                    }
                    is WatchTogether.Failed -> {
                        Text(state.error.title, style = MaterialTheme.typography.titleMedium, color = t.danger)
                        Text(state.error.body, style = MaterialTheme.typography.bodyLarge, color = t.textMuted)
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            if (state.error.canRetry) ShButton("Try again", onRetry, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
                            ShButton("Close", onCancel, if (state.error.canRetry) Modifier else Modifier.focusRequester(focus))
                        }
                    }
                    is WatchTogether.Ready -> {
                        Text("Room code", style = MaterialTheme.typography.labelLarge, color = t.textMuted)
                        Text(
                            RoomCode.display(state.room.code),
                            style = MaterialTheme.typography.displayLarge.copy(fontFamily = FontFamily.Monospace, fontSize = 64.sp, letterSpacing = 6.sp),
                            color = t.accent,
                        )
                        Text(
                            "On another TV: Search, then Join a room, and enter this code. On a phone or computer: scan the QR code" +
                                (state.shareUrl?.let { " or open $it" } ?: "") + ".",
                            style = MaterialTheme.typography.bodyMedium, color = t.textMuted,
                        )
                        when {
                            state.needsAudioConsent -> Text("This room's source has no confirmed audio in your languages. Start anyway to watch it with the audio it has.", style = MaterialTheme.typography.bodyMedium, color = t.text)
                            state.error != null -> Text("${state.error.title}. ${state.error.body}", style = MaterialTheme.typography.bodyMedium, color = t.danger)
                            else -> Text("Start when you're ready. The film waits, paused, until someone presses Play.", style = MaterialTheme.typography.bodyMedium, color = t.text)
                        }
                        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            ShButton(
                                when { state.starting -> "Starting…"; state.needsAudioConsent -> "Start with this audio"; else -> "Start" },
                                onStart, Modifier.focusRequester(focus), icon = HeroIcons.Play, style = ButtonStyle.Primary,
                            )
                            ShButton("Cancel room", onCancel)
                        }
                    }
                }
            }
            if (state is WatchTogether.Ready && state.shareUrl != null) QrCode(state.shareUrl, size = 220.dp)
        }
        // Re-armed when the dialog moves on (opening → code → failure): the focused button may have gone.
        LaunchedEffect(state::class) { withFrameNanos { }; runCatching { focus.requestFocus() } }
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
