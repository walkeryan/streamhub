package com.streamhub.tv.feature.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.Row
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.ui.components.FocusRow
import com.streamhub.tv.core.ui.components.ProgressLine
import com.streamhub.tv.core.ui.components.sizedImage
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

internal data class HomeCardCopy(val title: String, val detail: String, val onNow: Boolean, val progress: Float?)

/** On now means scheduled now, never an assertion that a sports event is live. */
internal fun homeCardCopy(tile: Tile, now: Instant): HomeCardCopy {
    val programme = tile.nowPlaying.takeIf { tile.kind == MediaKind.CHANNEL }
    val times = programme?.let { runCatching { Instant.parse(it.start) to Instant.parse(it.stop) }.getOrNull() }
    val airing = times != null && times.second > times.first && now >= times.first && now < times.second
    val timeLabel = times?.let {
        val format = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withZone(ZoneId.systemDefault())
        "${format.format(it.first)}–${format.format(it.second)}"
    }
    val detail = if (tile.kind == MediaKind.CHANNEL) listOfNotNull(tile.title, timeLabel).joinToString(" · ")
        else listOfNotNull(tile.year?.toString(), tile.sources.map { it.name }.distinct().take(2).joinToString(" · ").takeIf { it.isNotBlank() }).joinToString(" · ")
    val progress = if (airing && times != null) ((now.toEpochMilli() - times.first.toEpochMilli()).toFloat() / (times.second.toEpochMilli() - times.first.toEpochMilli())).coerceIn(0f, 1f)
        else if (tile.kind == MediaKind.CHANNEL) null else tile.progressPct?.takeIf { it > 0 }?.let { (it / 100.0).toFloat().coerceIn(0f, 1f) }
    return HomeCardCopy(programme?.title ?: tile.title, detail, airing, progress)
}

/** Home-only wide cards leave shared Browse/Live widgets and their navigation untouched. */
@Composable
internal fun HomeProgrammeRow(row: Row, now: Instant, onPlay: (Tile) -> Unit, onOpen: (Tile) -> Unit, onFocusTile: (Tile) -> Unit) {
    FocusRow(items = row.tiles, key = { it.id }, title = row.title) { _, tile, modifier ->
        HomeProgrammeCard(tile, now, modifier, onFocusTile) {
            if (tile.kind == MediaKind.CHANNEL || (tile.progressPct ?: 0.0) > 0 && tile.playableInApp) onPlay(tile) else onOpen(tile)
        }
    }
}

@Composable
private fun HomeProgrammeCard(tile: Tile, now: Instant, modifier: Modifier, onFocusTile: (Tile) -> Unit, onClick: () -> Unit) {
    val theme = LocalThemeTokens.current
    val copy = remember(tile, now) { homeCardCopy(tile, now) }
    val channel = tile.kind == MediaKind.CHANNEL
    // Reuse existing safe tile artwork; do not start rendering raw Programme.icon URLs.
    val image = tile.backdrop ?: if (channel) tile.logo ?: tile.poster else tile.poster
    val logoOnly = channel && tile.backdrop == null && tile.logo != null
    var imageFailed by remember(image) { mutableStateOf(false) }
    FocusSurface(
        onClick = onClick,
        modifier = modifier.width(288.dp).onFocusChanged { if (it.isFocused) onFocusTile(tile) },
        shape = RoundedCornerShape(Tokens.Radius.Lg),
        scaleOnFocus = false,
    ) {
        Column {
            Box(Modifier.fillMaxWidth().height(162.dp).background(theme.surfaceAlt)) {
                if (image != null && !imageFailed) {
                    AsyncImage(
                        model = sizedImage(image, 288.dp, 162.dp), contentDescription = null,
                        contentScale = if (logoOnly) ContentScale.Fit else ContentScale.Crop,
                        modifier = Modifier.fillMaxSize().then(if (logoOnly) Modifier.padding(20.dp) else Modifier),
                        onError = { imageFailed = true },
                    )
                } else {
                    Text(tile.title, color = theme.text, style = MaterialTheme.typography.titleMedium,
                        maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.align(Alignment.Center).padding(20.dp))
                }
                if (copy.onNow) {
                    Text("On now", color = theme.text, style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.align(Alignment.BottomStart).padding(10.dp)
                            .background(theme.surface, RoundedCornerShape(Tokens.Radius.Sm)).padding(horizontal = 8.dp, vertical = 4.dp))
                }
                copy.progress?.let { ProgressLine(it, Modifier.align(Alignment.BottomCenter)) }
            }
            Column(Modifier.fillMaxWidth().height(96.dp).padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(copy.detail, color = theme.textMuted, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(copy.title, color = theme.text, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}
