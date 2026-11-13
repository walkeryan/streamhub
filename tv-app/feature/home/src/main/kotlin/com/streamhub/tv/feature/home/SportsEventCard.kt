package com.streamhub.tv.feature.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.SportsEvent
import com.streamhub.tv.core.data.repo.recordingStateMark
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import java.time.ZoneId

/** A few words on a filled tag: LIVE, HD. Text carries the meaning, the fill only the weight. */
@Composable
private fun Tag(text: String, fill: Color, ink: Color) {
    Text(
        text, color = ink, style = MaterialTheme.typography.labelSmall, maxLines = 1,
        modifier = Modifier.background(fill, RoundedCornerShape(4.dp)).padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

/** A game that is on: what it is, where, and since when. Select plays it (or asks which channel). */
@Composable
internal fun SportsLiveCard(event: SportsEvent, zone: ZoneId, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val t = LocalThemeTokens.current
    FocusSurface(onClick = onClick, modifier = modifier.size(Tokens.Tile.LandscapeW, Tokens.Tile.LandscapeH), containerColor = t.surfaceAlt) {
        Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                if (event.liveEvidence && !event.replay) Tag(sportsLiveBadge(event), t.danger, Color.White) else Tag(sportsLiveBadge(event), t.overlay, t.text)
                sportsQuality(event)?.let { Tag(it, t.overlay, t.text) }
                Text(event.sport, color = t.textMuted, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Text(sportsEventTitle(event), color = t.text, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text(sportsLeagueLine(event), color = t.textMuted, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(sportsStarted(event, zone), color = t.textMuted, style = MaterialTheme.typography.labelSmall, maxLines = 1)
        }
    }
}

/** A game still to come: its local time first. [recordingState] puts the recorder's mark beside it. */
@Composable
internal fun SportsUpcomingCard(event: SportsEvent, zone: ZoneId, recordingState: String?, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val t = LocalThemeTokens.current
    FocusSurface(onClick = onClick, modifier = modifier.size(Tokens.Tile.LandscapeW, 112.dp), containerColor = t.surfaceAlt) {
        Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                listOfNotNull(sportsTime(event.start, zone).takeIf { it.isNotEmpty() }, recordingStateMark(recordingState)).joinToString("  "),
                color = t.accent, style = MaterialTheme.typography.labelLarge, maxLines = 1,
            )
            Text(sportsEventTitle(event), color = t.text, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text("${event.sport} · ${sportsLeagueLine(event)}", color = t.textMuted, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/** A channel carrying a game without saying when: named, and nothing claimed about live or upcoming. */
@Composable
internal fun SportsGameFeedCard(event: SportsEvent, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val t = LocalThemeTokens.current
    FocusSurface(onClick = onClick, modifier = modifier.size(Tokens.Tile.LandscapeW, Tokens.Tile.ChannelH), containerColor = t.surfaceAlt) {
        Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                sportsQuality(event)?.let { Tag(it, t.overlay, t.text) }
                Text(event.sport, color = t.textMuted, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Text(sportsEventTitle(event), color = t.text, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text(sportsLeagueLine(event), color = t.textMuted, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}
