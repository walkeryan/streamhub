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
 * True when [accepted] (a stored acceptance version, e.g. "1.0") covers [required]. Versions compare
 * numerically per dot-separated part, so accepting the server's newer text (say 1.1) also satisfies
 * the built-in 1.0 copy instead of re-showing the gate on every launch.
 */
fun isDisclaimerAccepted(accepted: String?, required: String = DISCLAIMER_VERSION): Boolean {
    if (accepted.isNullOrBlank()) return false
    val a = accepted.split('.').map { it.trim().toIntOrNull() ?: return accepted == required }
    val r = required.split('.').map { it.trim().toIntOrNull() ?: return accepted == required }
    for (i in 0 until maxOf(a.size, r.size)) {
        val x = a.getOrElse(i) { 0 }
        val y = r.getOrElse(i) { 0 }
        if (x != y) return x > y
    }
    return true
}

/**
 * docs/LEGAL.md gate: full-screen, "I accept" (default focus) + "Exit"; Accept disabled for 3 s.
 * Shown on first run before anything else (onboarding), and reused by the Add Source flow.
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

