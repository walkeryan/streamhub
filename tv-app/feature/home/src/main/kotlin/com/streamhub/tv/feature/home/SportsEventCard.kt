package com.streamhub.tv.feature.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.SportsEvent
import com.streamhub.tv.core.data.model.SportsFeed
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

internal fun sportsCanWatch(event: SportsEvent, now: Instant): Boolean = event.status == "current" &&
    runCatching { now >= Instant.parse(event.start) && now < Instant.parse(event.stop) }.getOrDefault(false)

internal fun sportsStatusFilterLabel(status: String): String = when(status) {
    "current" -> "On now"
    "upcoming" -> "Upcoming"
    "replay" -> "Replay listings"
    "ended" -> "Earlier broadcasts"
    else -> "All statuses"
}

internal fun sportsEventBadge(event: SportsEvent): String = when {
    event.replay -> "Replay listing"
    event.status == "current" && event.liveEvidence -> "LIVE · guide tagged"
    else -> sportsStatusFilterLabel(event.status)
}

internal fun sportsEventTime(event: SportsEvent): String = runCatching {
    val fmt = DateTimeFormatter.ofPattern("h:mm a").withZone(ZoneId.of("America/New_York"))
    "${fmt.format(Instant.parse(event.start))}–${fmt.format(Instant.parse(event.stop))}"
}.getOrDefault("Schedule time unavailable")

/** Feed choice is not audio consent; the global playback coordinator performs that check. */
internal fun sportsFeedPlaybackId(event: SportsEvent, feed: SportsFeed, now: Instant): String? =
    feed.channelId.takeIf { sportsCanWatch(event,now) && event.feeds.any { it.channelId == feed.channelId && it.programmeId == feed.programmeId } }

@Composable
internal fun SportsEventCard(event: SportsEvent, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val t = LocalThemeTokens.current
    FocusSurface(onClick = onClick, modifier = modifier.width(270.dp), shape = RoundedCornerShape(20.dp), scaleOnFocus = false) {
        Column(Modifier.fillMaxWidth().height(250.dp).background(t.surfaceAlt).padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(sportsEventBadge(event), color = t.accent, style = MaterialTheme.typography.labelLarge)
            Text(event.title, color = t.text, style = MaterialTheme.typography.titleLarge, maxLines = 4, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text(sportsEventTime(event), color = t.text, style = MaterialTheme.typography.bodyLarge)
            Text("${event.sport} · ${event.feeds.size} feed${if(event.feeds.size == 1) "" else "s"}", color = t.textMuted, style = MaterialTheme.typography.labelMedium)
            Text(event.feeds.map { it.channelName }.distinct().joinToString(" · "), color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
internal fun SportsFeedDialog(event: SportsEvent, onDismiss: () -> Unit, onWatch: (SportsFeed) -> Unit) {
    val t = LocalThemeTokens.current
    val closeFocus = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        Column(Modifier.width(640.dp).heightIn(max = 540.dp).background(t.surface, RoundedCornerShape(20.dp)).padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(event.title, color = t.text, style = MaterialTheme.typography.titleLarge, maxLines = 2)
            Text("${sportsEventBadge(event)} · ${sportsEventTime(event)}", color = t.textMuted)
            ShButton("Close", onDismiss, Modifier.focusRequester(closeFocus))
            if (!sportsCanWatch(event, Instant.now())) Text("Not airing now. These feed listings do not provide catch-up playback.", color = t.textMuted)
            Text("Guide-matched feeds. Playback checks audio and asks once if it is unverified.", color = t.textMuted)
            LazyColumn(Modifier.weight(1f, fill=false), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(event.feeds, key = { "${it.channelId}:${it.programmeId}" }) { feed ->
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("${feed.sourceName} · ${feed.channelName}", color = t.text)
                        Text("${if(feed.languageVerified) "Audio" else "Provider language hint"}: ${feed.language ?: "Unknown"}", color = t.textMuted)
                        if(sportsCanWatch(event, Instant.now())) ShButton("Choose feed", { if(sportsFeedPlaybackId(event,feed,Instant.now()) != null) onWatch(feed) })
                    }
                }
            }
        }
    }
    LaunchedEffect(Unit) { runCatching { closeFocus.requestFocus() } }
}
