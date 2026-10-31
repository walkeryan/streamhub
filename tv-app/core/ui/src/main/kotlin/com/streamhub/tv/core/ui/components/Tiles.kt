package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
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
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import coil3.request.ImageRequest
import coil3.request.crossfade
import coil3.size.Size
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import java.time.Instant

/** Image request sized to the tile (BRIEF performance budget: "images sized to tile"). */
@Composable
fun sizedImage(url: String?, w: Dp, h: Dp): ImageRequest {
    val d = LocalDensity.current
    return ImageRequest.Builder(LocalContext.current)
        .data(url)
        .size(Size(with(d) { w.roundToPx() }, with(d) { h.roundToPx() }))
        .crossfade(Tokens.Motion.FAST_MS)
        .build()
}

fun programmeProgress(p: Programme?, now: Instant = Instant.now()): Float? {
    p ?: return null
    return runCatching {
        val s = Instant.parse(p.start).toEpochMilli()
        val e = Instant.parse(p.stop).toEpochMilli()
        ((now.toEpochMilli() - s).toFloat() / (e - s).coerceAtLeast(1)).coerceIn(0f, 1f)
    }.getOrNull()
}

/** 2:3 poster. Title shows under the tile only when focused or there is no artwork (BRIEF Tiles). */
@Composable
fun PosterTile(
    tile: Tile,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onFocus: (Tile) -> Unit = {},
    onLongClick: (() -> Unit)? = null,
) {
    val t = LocalThemeTokens.current
    var isFocused by remember { mutableStateOf(false) }
    Column(modifier.width(Tokens.Tile.PosterW)) {
        FocusSurface(
            onClick = onClick,
            onLongClick = onLongClick,
            modifier = Modifier
                .size(Tokens.Tile.PosterW, Tokens.Tile.PosterH)
                .onFocusChanged { isFocused = it.isFocused; if (it.isFocused) onFocus(tile) },
        ) {
            if (tile.poster != null) {
                AsyncImage(
                    model = sizedImage(tile.poster, Tokens.Tile.PosterW, Tokens.Tile.PosterH),
                    contentDescription = tile.title,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            } else {
                Text(
                    tile.title,
                    modifier = Modifier.align(Alignment.Center).padding(12.dp),
                    style = MaterialTheme.typography.titleSmall,
                    color = t.text,
                    maxLines = 4,
                )
            }
            tile.progressPct?.takeIf { it > 0 }?.let {
                ProgressLine((it / 100.0).toFloat(), Modifier.align(Alignment.BottomCenter).padding(6.dp))
            }
            SourceBadges(tile.sources, Modifier.align(Alignment.BottomEnd).padding(6.dp).padding(bottom = 8.dp), max = 1)
        }
        Box(Modifier.height(40.dp).padding(top = 8.dp)) {
            if (isFocused || tile.poster == null) {
                Text(tile.title, style = MaterialTheme.typography.labelLarge, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/** 16:9 landscape tile (Continue Watching, episodes). */
@Composable
fun LandscapeTile(
    tile: Tile,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onFocus: (Tile) -> Unit = {},
) {
    val t = LocalThemeTokens.current
    Column(modifier.width(Tokens.Tile.LandscapeW)) {
        FocusSurface(
            onClick = onClick,
            modifier = Modifier.size(Tokens.Tile.LandscapeW, Tokens.Tile.LandscapeH).onFocusChanged { if (it.isFocused) onFocus(tile) },
        ) {
            AsyncImage(
                model = sizedImage(tile.backdrop ?: tile.poster, Tokens.Tile.LandscapeW, Tokens.Tile.LandscapeH),
                contentDescription = tile.title,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
            Box(Modifier.fillMaxSize().background(Brush.verticalGradient(0.5f to Color.Transparent, 1f to t.scrimBottom)))
            Column(Modifier.align(Alignment.BottomStart).padding(10.dp)) {
                Text(tile.title, style = MaterialTheme.typography.labelLarge, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                tile.subtitle?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = t.textMuted, maxLines = 1) }
                tile.progressPct?.takeIf { it > 0 }?.let { ProgressLine((it / 100.0).toFloat(), Modifier.padding(top = 6.dp)) }
            }
            SourceBadges(tile.sources, Modifier.align(Alignment.TopEnd).padding(6.dp), max = 1)
        }
    }
}

/** Live channel tile: logo on colour block, number, now-playing + progress. No blur on scrolling content. */
@Composable
fun ChannelTile(
    title: String,
    number: Int?,
    logo: String?,
    now: Programme?,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onFocus: () -> Unit = {},
) {
    val t = LocalThemeTokens.current
    FocusSurface(
        onClick = onClick,
        modifier = modifier.size(Tokens.Tile.ChannelW, Tokens.Tile.ChannelH).onFocusChanged { if (it.isFocused) onFocus() },
        containerColor = t.surfaceAlt,
    ) {
        Row(Modifier.fillMaxSize().padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(56.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Sm)), contentAlignment = Alignment.Center) {
                if (logo != null) {
                    AsyncImage(sizedImage(logo, 56.dp, 56.dp), contentDescription = title, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize().padding(4.dp))
                } else {
                    Text(title.take(2).uppercase(), color = t.text, style = MaterialTheme.typography.titleSmall)
                }
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    listOfNotNull(number?.toString(), title).joinToString("  "),
                    style = MaterialTheme.typography.labelLarge, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Text(now?.title ?: "No programme info", style = MaterialTheme.typography.labelSmall, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                programmeProgress(now)?.let { ProgressLine(it, Modifier.padding(top = 6.dp), height = 3.dp) }
            }
        }
    }
}

@Composable
fun ChannelTile(channel: Channel, onClick: () -> Unit, modifier: Modifier = Modifier, onFocus: () -> Unit = {}) =
    ChannelTile(channel.name, channel.number, channel.logo, channel.now, onClick, modifier, onFocus)

@Composable
fun TileFor(
    tile: Tile,
    layout: com.streamhub.tv.core.data.model.RowLayout,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onFocus: (Tile) -> Unit = {},
) = when (layout) {
    com.streamhub.tv.core.data.model.RowLayout.LANDSCAPE,
    com.streamhub.tv.core.data.model.RowLayout.HERO -> LandscapeTile(tile, onClick, modifier, onFocus)
    com.streamhub.tv.core.data.model.RowLayout.CHANNEL ->
        ChannelTile(tile.title, null, tile.logo ?: tile.poster, tile.nowPlaying, onClick, modifier) { onFocus(tile) }
    com.streamhub.tv.core.data.model.RowLayout.POSTER -> PosterTile(tile, onClick, modifier, onFocus)
}

/** Row header spacing helper. */
val RowGap = Tokens.Tile.Gap

