package com.streamhub.tv.core.ui.components

import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.ui.theme.LocalReducedMotion
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** Full-bleed backdrop that crossfades (420 ms) when [tile] changes. Static overlay → no layout shift. */
@Composable
fun HeroBackdrop(tile: Tile?, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    val dur = if (LocalReducedMotion.current) 0 else Tokens.Motion.HERO_CROSSFADE_MS
    Box(modifier.fillMaxSize()) {
        Crossfade(targetState = tile?.backdrop ?: tile?.poster, animationSpec = tween(dur), label = "hero") { url ->
            if (url != null) {
                AsyncImage(model = url, contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize())
            } else {
                Box(Modifier.fillMaxSize().background(t.bg))
            }
        }
        // Scrims: bottom + left so text and rows stay legible (contrast ≥ 4.5:1 on body text).
        Box(Modifier.fillMaxSize().background(Brush.verticalGradient(0f to t.scrimTop, 0.55f to t.scrimTop, 1f to t.bg)))
        Box(Modifier.fillMaxSize().background(Brush.horizontalGradient(0f to t.scrimBottom, 0.6f to Color.Transparent)))
    }
}

fun Tile.metaLine(): String = listOfNotNull(
    year?.toString(),
    rating?.let { "★ %.1f".format(it) },
    subtitle,
).joinToString("  ·  ")

/** Title/meta/buttons block of the billboard. Buttons are passed by the caller (they own focus). */
@Composable
fun HeroInfo(tile: Tile?, modifier: Modifier = Modifier, height: Dp = 220.dp, buttons: @Composable () -> Unit = {}) {
    val t = LocalThemeTokens.current
    val dur = if (LocalReducedMotion.current) 0 else Tokens.Motion.HERO_CROSSFADE_MS
    Column(modifier.height(height).width(560.dp), verticalArrangement = Arrangement.Bottom) {
        Crossfade(targetState = tile, animationSpec = tween(dur), label = "heroText") { tl ->
            Column {
                if (tl?.logo != null) {
                    AsyncImage(model = tl.logo, contentDescription = tl.title, contentScale = ContentScale.Fit, alignment = Alignment.CenterStart, modifier = Modifier.height(80.dp).fillMaxWidth())
                } else {
                    Text(tl?.title.orEmpty(), style = MaterialTheme.typography.displayMedium, color = t.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
                if (tl != null) {
                    Text(tl.metaLine(), style = MaterialTheme.typography.bodyLarge, color = t.textMuted, maxLines = 1, modifier = Modifier.padding(top = 8.dp))
                    SourceBadges(tl.sources, Modifier.padding(top = 8.dp), max = 3)
                }
            }
        }
        Row(Modifier.padding(top = 16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) { buttons() }
    }
}

object HeroIcons {
    val Play = Icons.Filled.PlayArrow
    val Open = Icons.AutoMirrored.Filled.OpenInNew
    val Info = Icons.Filled.Info
}
