package com.streamhub.tv.feature.sources

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.LiveTv
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.PlaylistPlay
import androidx.compose.material.icons.filled.Subscriptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Icon
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.Source
import com.streamhub.tv.core.data.model.SourceType
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.DISCLAIMER_TEXT
import com.streamhub.tv.core.ui.components.DISCLAIMER_VERSION
import com.streamhub.tv.core.ui.components.DisclaimerGate
import com.streamhub.tv.core.ui.components.FocusRow
import com.streamhub.tv.core.ui.components.HandoffQrDialog
import com.streamhub.tv.core.ui.components.QrCode
import com.streamhub.tv.core.ui.components.SafeArea
import com.streamhub.tv.core.ui.components.SectionTitle
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShTextField
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

private data class AddCard(val type: SourceType, val title: String, val body: String, val icon: ImageVector)

private val ADD_CARDS = listOf(
    AddCard(SourceType.PLEX, "Plex", "Link with a PIN or QR code", Icons.Filled.Movie),
    AddCard(SourceType.JELLYFIN, "Jellyfin", "Server URL + login or Quick Connect", Icons.Filled.Dns),
    AddCard(SourceType.XTREAM, "Xtream Codes", "IPTV provider credentials", Icons.Filled.LiveTv),
    AddCard(SourceType.M3U, "M3U playlist", "Playlist URL + optional EPG", Icons.Filled.PlaylistPlay),
    AddCard(SourceType.STREAMING_SERVICE, "Streaming services", "Netflix, Disney+, Hulu… (Open in app)", Icons.Filled.Subscriptions),
)

@Composable
fun SourcesScreen(vm: SourcesViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    when (val f = s.flow) {
        AddFlow.None -> SourcesHome(s, vm)
        AddFlow.Disclaimer -> {
            BackHandler { vm.cancelFlow() }
            DisclaimerGate(DISCLAIMER_TEXT, DISCLAIMER_VERSION, onAccept = vm::acceptDisclaimer, onExit = vm::cancelFlow, exitLabel = "Cancel")
        }
        is AddFlow.Plex -> { BackHandler { vm.cancelFlow() }; PlexLink(f, vm::cancelFlow) }
        is AddFlow.Form -> { BackHandler { vm.cancelFlow() }; SourceForm(f.type, s.busy, s.message, vm::submit, vm::cancelFlow) }
        AddFlow.Services -> { BackHandler { vm.cancelFlow() }; ServicesGrid(s, vm) }
    }
    s.qr?.let { HandoffQrDialog(it.webUrl, it.serviceName, vm::dismissQr) }
}

@Composable
private fun SourcesHome(s: SourcesState, vm: SourcesViewModel) {
    val t = LocalThemeTokens.current
    val rail = LocalRailController.current
    PivotScroll(0.dp) {
        LazyColumn(Modifier.fillMaxSize().focusRestorer(), contentPadding = PaddingValues(vertical = Tokens.Canvas.SafeMarginY)) {
            item("hdr") {
                Text("Sources", style = MaterialTheme.typography.headlineMedium, color = t.text, modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX))
                s.message?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = t.accent, modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX, top = 4.dp)) }
            }
            item("add") {
                FocusRow(ADD_CARDS, key = { it.type }, title = "Add a source") { _, card, m ->
                    FocusSurface(onClick = { vm.startAdd(card.type) }, modifier = m.size(220.dp, 120.dp)) {
                        Column(Modifier.padding(16.dp)) {
                            Icon(card.icon, null, tint = t.accent, modifier = Modifier.size(28.dp))
                            Text(card.title, style = MaterialTheme.typography.titleSmall, color = t.text, modifier = Modifier.padding(top = 8.dp))
                            Text(card.body, style = MaterialTheme.typography.labelMedium, color = t.textMuted, maxLines = 2)
                        }
                    }
                }
            }
            item("listTitle") { SectionTitle("Your sources", Modifier.padding(start = Tokens.Canvas.SafeMarginX, top = 16.dp, bottom = 8.dp)) }
            if (s.sources.isEmpty() && !s.loading) item("empty") {
                Text("No sources yet.", color = t.textMuted, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX))
            }
            items(s.sources, key = { it.id }) { src -> SourceRow(src, vm, Modifier.leftOpensRail(rail)) }
        }
    }
}

@Composable
private fun SourceRow(src: Source, vm: SourcesViewModel, firstModifier: Modifier) {
    val t = LocalThemeTokens.current
    Row(
        Modifier.fillMaxWidth().padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = 4.dp)
            .background(t.surface, RoundedCornerShape(Tokens.Radius.Md)).padding(12.dp)
            .focusRestorer(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(src.name, style = MaterialTheme.typography.titleSmall, color = t.text)
            val status = when (src.status) { "ok" -> "✓ OK"; "syncing" -> "⟳ Syncing"; "error" -> "⚠ Error"; else -> "⚠ Sign-in needed" }
            Text(
                listOfNotNull(src.type.name.lowercase().replace('_', ' '), status, src.counts.entries.joinToString(" · ") { "${it.value} ${it.key}" }.ifBlank { null }).joinToString("  ·  "),
                style = MaterialTheme.typography.labelMedium, color = if (src.status == "ok" || src.status == "syncing") t.textMuted else t.danger,
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShButton("Sync", { vm.sync(src) }, firstModifier)
            ShButton("Test", { vm.test(src) })
            ShButton("Remove", { vm.delete(src) }, style = ButtonStyle.Danger)
        }
    }
}

@Composable
private fun PlexLink(f: AddFlow.Plex, onCancel: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    Row(Modifier.fillMaxSize().padding(SafeArea), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(48.dp)) {
        Column(Modifier.weight(1f)) {
            Text("Link Plex", style = MaterialTheme.typography.displaySmall, color = t.text)
            if (f.pin == null) {
                Text(f.error ?: "Requesting a PIN…", color = if (f.error != null) t.danger else t.textMuted, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(top = 12.dp))
            } else {
                Text("Go to plex.tv/link and enter:", style = MaterialTheme.typography.bodyLarge, color = t.textMuted, modifier = Modifier.padding(top = 12.dp))
                Text(f.pin.code, style = MaterialTheme.typography.displayLarge, color = t.accent, modifier = Modifier.padding(vertical = 12.dp))
                Text("…or scan the QR code. This screen continues automatically once linked.", style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
            }
            ShButton("Cancel", onCancel, Modifier.padding(top = 24.dp).focusRequester(focus))
        }
        f.pin?.let { QrCode(it.linkUrl, size = 260.dp) }
    }
}

private data class Field(val key: String, val label: String, val placeholder: String = "", val password: Boolean = false, val type: KeyboardType = KeyboardType.Text)

private fun fieldsFor(type: SourceType): List<Field> = when (type) {
    SourceType.JELLYFIN -> listOf(
        Field("server_url", "Server URL", "http://192.168.1.20:8096", type = KeyboardType.Uri),
        Field("username", "Username"),
        Field("password", "Password", password = true, type = KeyboardType.Password),
    )
    SourceType.XTREAM -> listOf(
        Field("server_url", "Server URL", "http://provider.example:8080", type = KeyboardType.Uri),
        Field("username", "Username"),
        Field("password", "Password", password = true, type = KeyboardType.Password),
        Field("output", "Output (ts or m3u8)", "ts"),
    )
    SourceType.M3U -> listOf(
        Field("url", "Playlist URL", "https://…/playlist.m3u", type = KeyboardType.Uri),
        Field("epg_url", "EPG (XMLTV) URL — optional", type = KeyboardType.Uri),
        Field("user_agent", "User-Agent — optional"),
        Field("referer", "Referer — optional", type = KeyboardType.Uri),
        Field("refresh_hours", "Auto-refresh every N hours", "24", type = KeyboardType.Number),
    )
    else -> emptyList()
}

/** Credentials are sent once to the server (encrypted at rest there) and never stored on the TV. */
@Composable
private fun SourceForm(type: SourceType, busy: Boolean, message: String?, onSubmit: (SourceType, String, Map<String, String>) -> Unit, onCancel: () -> Unit) {
    val t = LocalThemeTokens.current
    val values = remember(type) { mutableStateMapOf<String, String>() }
    var name by remember(type) { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    LaunchedEffect(type) { runCatching { focus.requestFocus() } }
    PivotScroll(96.dp) {
        LazyColumn(Modifier.fillMaxSize().focusRestorer(), contentPadding = SafeArea, verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item { Text("Add ${type.name.lowercase().replaceFirstChar { it.uppercase() }}", style = MaterialTheme.typography.headlineMedium, color = t.text) }
            if (type == SourceType.JELLYFIN) item {
                Text("Tip: leave username/password empty to use Quick Connect from your Jellyfin dashboard.", style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
            }
            item { ShTextField(name, { name = it }, "Display name", Modifier.width(560.dp).focusRequester(focus), placeholder = type.name.lowercase()) }
            items(fieldsFor(type), key = { it.key }) { f ->
                ShTextField(values[f.key].orEmpty(), { values[f.key] = it }, f.label, Modifier.width(560.dp), placeholder = f.placeholder, password = f.password, keyboardType = f.type)
            }
            item {
                message?.let { Text(it, color = t.danger, style = MaterialTheme.typography.bodyMedium) }
                Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    val payload = values.toMap() + if (type == SourceType.JELLYFIN && values["username"].isNullOrBlank()) mapOf("quick_connect" to "true") else emptyMap()
                    ShButton(if (busy) "Adding…" else "Add source", { onSubmit(type, name, payload) }, style = ButtonStyle.Primary, enabled = !busy)
                    ShButton("Cancel", onCancel)
                }
            }
        }
    }
}

@Composable
private fun ServicesGrid(s: SourcesState, vm: SourcesViewModel) {
    val t = LocalThemeTokens.current
    val enabled = s.sources.filter { it.type == SourceType.STREAMING_SERVICE }.mapNotNull { it.serviceKey }.toSet()
    Column(Modifier.fillMaxSize().padding(SafeArea)) {
        Text("Streaming services", style = MaterialTheme.typography.headlineMedium, color = t.text)
        Text(
            "Turn on the services you subscribe to. Their titles show \"Open in app\" and launch the installed app. Names are trademarks of their owners; StreamHub is not affiliated with them.",
            style = MaterialTheme.typography.bodyMedium, color = t.textMuted, modifier = Modifier.padding(top = 4.dp, bottom = 16.dp).width(760.dp),
        )
        s.message?.let { Text(it, color = t.accent, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(bottom = 8.dp)) }
        LazyVerticalGrid(GridCells.Fixed(2), Modifier.fillMaxSize().focusRestorer(), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            items(s.registry, key = { it.key }) { e ->
                val on = e.key in enabled
                val installed = vm.isInstalled(e.androidPackage)
                Row(Modifier.fillMaxWidth().height(64.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Md)).padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(e.name, style = MaterialTheme.typography.titleSmall, color = t.text)
                        Text(
                            (if (installed) "Installed" else "Not installed") + if (e.supportsTitleLink) " · opens titles" else " · opens app",
                            style = MaterialTheme.typography.labelSmall, color = t.textMuted,
                        )
                    }
                    ShButton(if (on) "✓ On" else "Off", { vm.toggleService(e) }, style = if (on) ButtonStyle.Primary else ButtonStyle.Secondary)
                    Box(Modifier.width(8.dp))
                    ShButton("Test link", { vm.testLink(e) })
                }
            }
        }
    }
}
