package com.streamhub.tv.feature.livetv

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Icon
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.player.VideoSurface
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.ProgressLine
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.components.programmeProgress
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

private val HHMM = DateTimeFormatter.ofPattern("HH:mm")
internal fun hhmm(iso: String): String = runCatching { ZonedDateTime.parse(iso).withZoneSameInstant(ZoneId.systemDefault()).format(HHMM) }.getOrDefault("")

@Composable
fun LiveTvScreen(vm: LiveTvViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val launcher = LocalPlayLauncher.current
    val rail = LocalRailController.current
    DisposableEffect(Unit) { onDispose { vm.stopPreview() } }

    Column(Modifier.fillMaxSize().padding(vertical = Tokens.Canvas.SafeMarginY)) {
        // Language chips (profile languages + All) and favorites filter.
        LazyRow(
            Modifier.fillMaxWidth().focusRestorer(),
            contentPadding = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            item("fav") {
                ShChip("Favorites", s.favoritesOnly, vm::toggleFavoritesOnly, Modifier.leftOpensRail(rail), leading = Icons.Filled.Favorite)
            }
            item("all") { ShChip("All languages", s.selectedLanguages == null, { vm.toggleLanguage(null) }) }
            val shown = (s.profileLanguages + s.languages.map { it.code }).distinct()
            items(shown, key = { "l-$it" }) { code ->
                val name = s.languages.firstOrNull { it.code == code }?.name ?: code.uppercase()
                ShChip(name, s.selectedLanguages?.contains(code) == true, { vm.toggleLanguage(code) })
            }
        }
        // Groups
        LazyRow(
            Modifier.fillMaxWidth().padding(top = 10.dp).focusRestorer(),
            contentPadding = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            item("all-groups") { ShChip("All groups", s.selectedGroup == null, { vm.selectGroup(null) }, Modifier.leftOpensRail(rail)) }
            items(s.groups, key = { it.id }) { g -> ShChip("${g.name} · ${g.channelCount}", s.selectedGroup == g.name, { vm.selectGroup(g.name) }) }
        }

        Row(Modifier.fillMaxSize().padding(top = 16.dp)) {
            // Channel list (left)
            Box(Modifier.width(440.dp).fillMaxHeight().padding(start = Tokens.Canvas.SafeMarginX)) {
                when {
                    s.loading && s.channels.isEmpty() -> Text("Loading channels…", color = t.textMuted, style = MaterialTheme.typography.bodyLarge)
                    s.error != null -> Text("Couldn't load channels: ${s.error}", color = t.danger, style = MaterialTheme.typography.bodyLarge)
                    s.channels.isEmpty() -> Text(if (s.favoritesOnly) "No favorite channels yet. Long-press OK on a channel to add it." else "No channels match these filters.", color = t.textMuted, style = MaterialTheme.typography.bodyLarge)
                    else -> PivotScroll(leadingOffset = 72.dp) {
                        LazyColumn(
                            state = rememberLazyListState(),
                            modifier = Modifier.fillMaxSize().focusRestorer(),
                            verticalArrangement = Arrangement.spacedBy(6.dp),
                            contentPadding = PaddingValues(vertical = 8.dp, horizontal = 8.dp),
                        ) {
                            itemsIndexed(s.channels, key = { _, c -> c.id }) { _, ch ->
                                ChannelRow(
                                    ch,
                                    playing = ch.id == s.previewChannelId,
                                    onClick = { launcher.play(ch.id) },
                                    onLongClick = { vm.toggleFavorite(ch) },
                                    modifier = Modifier.leftOpensRail(rail).onFocusChanged { if (it.isFocused) vm.onChannelFocused(ch) },
                                )
                            }
                        }
                    }
                }
            }
            // Preview (right)
            Column(Modifier.weight(1f).padding(start = 24.dp, end = Tokens.Canvas.SafeMarginX)) {
                Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f).clip(RoundedCornerShape(Tokens.Radius.Md)).background(androidx.compose.ui.graphics.Color.Black)) {
                    VideoSurface(vm.preview, Modifier.fillMaxSize())
                    if (s.previewChannelId == null) {
                        Text("Focus a channel to preview", color = t.textMuted, modifier = Modifier.align(Alignment.Center), style = MaterialTheme.typography.bodyLarge)
                    }
                }
                val ch = s.channels.firstOrNull { it.id == s.previewChannelId }
                if (ch != null) {
                    Text("${ch.number}  ${ch.name}", style = MaterialTheme.typography.titleMedium, color = t.text, modifier = Modifier.padding(top = 12.dp))
                    ch.now?.let { Text("Now  ${hhmm(it.start)}–${hhmm(it.stop)}  ${it.title}", style = MaterialTheme.typography.bodyMedium, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                    ch.now?.description?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = t.textMuted, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp)) }
                    ch.next?.let { Text("Next ${hhmm(it.start)}  ${it.title}", style = MaterialTheme.typography.bodyMedium, color = t.textMuted, maxLines = 1, modifier = Modifier.padding(top = 4.dp)) }
                    Text("OK to watch full screen · long-press OK to favorite", style = MaterialTheme.typography.labelMedium, color = t.textMuted, modifier = Modifier.padding(top = 12.dp))
                }
            }
        }
    }
}

@Composable
private fun ChannelRow(ch: Channel, playing: Boolean, onClick: () -> Unit, onLongClick: () -> Unit, modifier: Modifier) {
    val t = LocalThemeTokens.current
    FocusSurface(
        onClick = onClick,
        onLongClick = onLongClick,
        modifier = modifier.fillMaxWidth().height(64.dp),
        containerColor = if (playing) t.surfaceAlt else t.surface,
        focusedContainerColor = t.surfaceAlt,
        scaleOnFocus = false,
        glow = false,
    ) {
        Row(Modifier.fillMaxSize().padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(ch.number.toString(), style = MaterialTheme.typography.labelLarge, color = t.textMuted, modifier = Modifier.width(44.dp))
            Box(Modifier.size(44.dp), contentAlignment = Alignment.Center) {
                if (ch.logo != null) AsyncImage(ch.logo, ch.name, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize())
                else Text(ch.name.take(2).uppercase(), color = t.text, style = MaterialTheme.typography.labelLarge)
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(ch.name, style = MaterialTheme.typography.labelLarge, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                    if (ch.isFavorite) Icon(Icons.Filled.Favorite, "Favorite", tint = t.accent, modifier = Modifier.padding(start = 6.dp).size(14.dp))
                    if (playing) Text("  ▶", color = t.accent, style = MaterialTheme.typography.labelSmall)
                }
                Text(ch.now?.title ?: "—", style = MaterialTheme.typography.labelSmall, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                programmeProgress(ch.now)?.let { ProgressLine(it, Modifier.padding(top = 3.dp), height = 2.dp) }
            }
        }
    }
}
