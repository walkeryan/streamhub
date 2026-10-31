package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.delay

/** Built-in copy of docs/LEGAL.md v1.0 — shown if the server is unreachable or before pairing. */
const val DISCLAIMER_VERSION = "1.0"
val DISCLAIMER_TEXT = """
StreamHub is a media player and organizer. It does not provide, host, sell, promote or distribute any channels, playlists, subscriptions, EPG data or streams.

You may only add sources you are legally authorized to access. You are solely responsible for the sources you configure and for complying with the laws of your jurisdiction and the rights of content owners. The authors and contributors of StreamHub accept no liability for how you use this software.

Streaming-service names and logos are trademarks of their respective owners. StreamHub is not affiliated with, endorsed by or certified by any of them, including Plex, Jellyfin, Netflix, Hulu, Disney, Amazon, Warner Bros. Discovery, Apple, NBCUniversal or Paramount.

This product uses the TMDB API but is not endorsed or certified by TMDB. Availability ("where to watch") data is provided by JustWatch.
""".trimIndent()

/**
 * docs/LEGAL.md gate: full-screen, "I accept" (default focus) + "Exit"; Accept disabled for 3 s.
 * Reused by Settings → About and the Add Source flow.
 */
@Composable
fun DisclaimerGate(
    text: String,
    version: String,
    onAccept: () -> Unit,
    onExit: () -> Unit,
    acceptLabel: String = "I accept",
    exitLabel: String = "Exit",
) {
    val t = LocalThemeTokens.current
    var remaining by remember { mutableIntStateOf(3) }
    val acceptFocus = remember { FocusRequester() }
    LaunchedEffect(Unit) {
        runCatching { acceptFocus.requestFocus() }
        while (remaining > 0) { delay(1000); remaining-- }
    }
    Box(Modifier.fillMaxSize().background(t.bg).padding(SafeArea), contentAlignment = Alignment.Center) {
        Column(Modifier.width(760.dp)) {
            Text("Legal notice & disclaimer", style = MaterialTheme.typography.headlineMedium, color = t.text)
            Text("Version $version", style = MaterialTheme.typography.labelMedium, color = t.textMuted, modifier = Modifier.padding(bottom = 16.dp))
            val scroll = rememberScrollState()
            Box(
                Modifier.fillMaxWidth().heightIn(max = 300.dp)
                    .background(t.surface, RoundedCornerShape(Tokens.Radius.Md))
                    .verticalScroll(scroll)
                    .padding(20.dp),
            ) {
                Text(text, style = MaterialTheme.typography.bodyLarge, color = t.text)
            }
            Row(Modifier.padding(top = 24.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                // Accept keeps focus while disabled (focusable but inert) so it is the default focus.
                ShButton(
                    if (remaining > 0) "$acceptLabel ($remaining)" else acceptLabel,
                    onClick = { if (remaining == 0) onAccept() },
                    modifier = Modifier.focusRequester(acceptFocus),
                    style = ButtonStyle.Primary,
                    enabled = remaining == 0,
                )
                ShButton(exitLabel, onExit)
            }
        }
    }
}

