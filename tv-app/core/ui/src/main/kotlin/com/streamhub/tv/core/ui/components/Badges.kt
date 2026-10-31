package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.SourceBadge
import com.streamhub.tv.core.data.model.SourceType
import com.streamhub.tv.core.ui.theme.LocalThemeTokens

fun SourceBadge.shortLabel(): String = when (sourceType) {
    SourceType.PLEX -> "Plex"
    SourceType.JELLYFIN -> "Jellyfin"
    SourceType.XTREAM, SourceType.M3U -> "IPTV"
    SourceType.STREAMING_SERVICE -> name
}

/** Small chips: source names + a "Play" (in-app) or "Open" (hand-off) action chip. Text, not colour, carries meaning. */
@Composable
fun SourceBadges(sources: List<SourceBadge>, modifier: Modifier = Modifier, max: Int = 2) {
    if (sources.isEmpty()) return
    val t = LocalThemeTokens.current
    val sorted = sources.sortedByDescending { it.playableInApp }
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        sorted.take(max).forEach { b ->
            Text(
                b.shortLabel(),
                style = MaterialTheme.typography.labelSmall,
                color = t.text,
                modifier = Modifier.background(t.overlay, RoundedCornerShape(4.dp)).padding(horizontal = 6.dp, vertical = 2.dp),
            )
        }
        val inApp = sorted.first().playableInApp
        Text(
            if (inApp) "▶ Play" else "↗ Open",
            style = MaterialTheme.typography.labelSmall,
            color = if (inApp) t.accentText else t.text,
            modifier = Modifier
                .background(if (inApp) t.accent else t.surfaceAlt, RoundedCornerShape(4.dp))
                .padding(horizontal = 6.dp, vertical = 2.dp),
        )
    }
}
