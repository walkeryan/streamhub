package com.streamhub.tv.feature.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.Settings
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.DISCLAIMER_TEXT
import com.streamhub.tv.core.ui.components.DISCLAIMER_VERSION
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.components.ShTextField
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.ThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** Two-pane settings: section list (left) · content (right). Right enters content, Left returns. */
@Composable
fun SettingsScreen(appVersion: String, vm: SettingsViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val rail = LocalRailController.current
    val first = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { first.requestFocus() } }
    DisposableEffect(Unit) { onDispose { vm.previewTheme(null) } }

    Row(Modifier.fillMaxSize().padding(vertical = Tokens.Canvas.SafeMarginY)) {
        Column(Modifier.width(260.dp).fillMaxHeight().padding(start = Tokens.Canvas.SafeMarginX).focusRestorer(first), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Settings", style = MaterialTheme.typography.headlineSmall, color = t.text, modifier = Modifier.padding(bottom = 12.dp))
            SettingsSection.entries.forEachIndexed { i, sec ->
                FocusSurface(
                    onClick = { vm.select(sec) },
                    modifier = Modifier.fillMaxWidth().height(44.dp).leftOpensRail(rail)
                        .then(if (i == 0) Modifier.focusRequester(first) else Modifier)
                        .onFocusChanged { if (it.isFocused) vm.select(sec) },
                    containerColor = if (s.section == sec) t.surfaceAlt else androidx.compose.ui.graphics.Color.Transparent,
                    focusedContainerColor = t.text, scaleOnFocus = false, glow = false,
                ) { focused ->
                    Text(sec.label, color = if (focused) t.bg else t.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.align(Alignment.CenterStart).padding(horizontal = 14.dp))
                }
            }
        }
        Box(Modifier.weight(1f).fillMaxHeight().padding(start = 32.dp, end = Tokens.Canvas.SafeMarginX)) {
            s.message?.let { Text(it, color = t.accent, style = MaterialTheme.typography.labelLarge, modifier = Modifier.align(Alignment.TopEnd)) }
            when (s.section) {
                SettingsSection.Profiles -> ProfilesPane(s, vm)
                SettingsSection.Theme -> ThemePane(vm)
                SettingsSection.Languages -> LanguagesPane(s, vm)
                SettingsSection.Rows -> RowsPane(s, vm)
                SettingsSection.Parental -> ParentalPane(s, vm)
                SettingsSection.Playback -> PlaybackPane(s, vm)
                SettingsSection.About -> AboutPane(s, appVersion, vm)
            }
        }
    }
    s.pinPromptFor?.let { p -> PinDialog("Enter PIN for ${p.name}", onSubmit = { vm.switchProfile(p, it) }, onDismiss = vm::cancelPin) }
}

@Composable
private fun Pane(title: String, content: androidx.compose.foundation.lazy.LazyListScope.() -> Unit) {
    val t = LocalThemeTokens.current
    PivotScroll(80.dp) {
        LazyColumn(Modifier.fillMaxSize().focusRestorer(), verticalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(bottom = 48.dp)) {
            item { Text(title, style = MaterialTheme.typography.titleLarge, color = t.text, modifier = Modifier.padding(bottom = 8.dp, top = 44.dp)) }
            content()
        }
    }
}

@Composable
private fun ProfilesPane(s: SettingsState, vm: SettingsViewModel) = Pane("Profiles") {
    items(s.profiles, key = { it.id }) { p ->
        val current = p.id == s.current?.id
        ShButton(
            (if (current) "✓ " else "") + p.name + (if (p.kids) " · Kids" else "") + (if (p.hasPin) " · 🔒" else ""),
            { vm.switchProfile(p) },
            style = if (current) ButtonStyle.Primary else ButtonStyle.Secondary,
        )
    }
    item { Text("Create and edit profiles from the web app (admin).", color = LocalThemeTokensText(), style = MaterialTheme.typography.bodySmall) }
}

@Composable private fun LocalThemeTokensText() = LocalThemeTokens.current.textMuted

/** Focusing a swatch previews the theme live across the whole app; OK commits. */
@Composable
private fun ThemePane(vm: SettingsViewModel) {
    val selected by vm.selectedTheme.collectAsStateWithLifecycle()
    val reduced by vm.reducedMotion.collectAsStateWithLifecycle()
    Pane("Theme") {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp), modifier = Modifier.focusRestorer()) {
                Tokens.themes.values.forEach { th ->
                    ThemeSwatch(th, th.id == selected, onFocus = { vm.previewTheme(th.id) }, onClick = { vm.selectTheme(th.id) })
                }
            }
        }
        item { Text("Focus a theme to preview it · OK to apply. Applies per profile.", color = LocalThemeTokens.current.textMuted, style = MaterialTheme.typography.bodySmall) }
        item {
            ShChip("Reduce motion", reduced, { vm.setReducedMotion(!reduced) }, Modifier.onFocusChanged { if (it.isFocused) vm.previewTheme(null) })
        }
    }
}

@Composable
private fun ThemeSwatch(th: ThemeTokens, selected: Boolean, onFocus: () -> Unit, onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        FocusSurface(onClick = onClick, modifier = Modifier.size(120.dp, 80.dp).onFocusChanged { if (it.isFocused) onFocus() }, containerColor = th.bg) {
            Column(Modifier.padding(10.dp)) {
                Box(Modifier.fillMaxWidth().height(14.dp).background(th.surface, RoundedCornerShape(3.dp)))
                Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Box(Modifier.size(20.dp).background(th.accent, RoundedCornerShape(10.dp)))
                    Box(Modifier.size(20.dp).border(2.dp, th.focusRing, RoundedCornerShape(4.dp)))
                    Text("Aa", color = th.text, style = MaterialTheme.typography.labelLarge)
                }
            }
        }
        Text((if (selected) "✓ " else "") + th.name, color = LocalThemeTokens.current.text, style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(top = 8.dp))
    }
}

@Composable
private fun LanguagesPane(s: SettingsState, vm: SettingsViewModel) = Pane("Live TV languages") {
    item { Text("Channels in these languages are shown by default in Live TV. None selected = all languages.", color = LocalThemeTokens.current.textMuted, style = MaterialTheme.typography.bodySmall) }
    items(s.languages, key = { it.code }) { l ->
        ShChip("${l.name} (${l.count})", s.current?.languages?.contains(l.code) == true, { vm.toggleLanguage(l.code) })
    }
}

@Composable
private fun RowsPane(s: SettingsState, vm: SettingsViewModel) = Pane("Home rows") {
    item { Text("Reorder or hide Home rows. ▲/▼ buttons move a row; Hide removes it from Home.", color = LocalThemeTokens.current.textMuted, style = MaterialTheme.typography.bodySmall) }
    itemsIndexed(s.rows, key = { _, r -> r.id }) { i, r ->
        val hidden = r.id in s.hiddenRows
        Row(Modifier.focusRestorer(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("${i + 1}. ${r.title}" + if (hidden) " (hidden)" else "", color = LocalThemeTokens.current.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.width(320.dp))
            ShButton("▲", { vm.moveRow(r.id, -1) }, enabled = i > 0)
            ShButton("▼", { vm.moveRow(r.id, +1) }, enabled = i < s.rows.lastIndex)
            ShButton(if (hidden) "Show" else "Hide", { vm.toggleRowHidden(r.id) })
        }
    }
}

@Composable
private fun ParentalPane(s: SettingsState, vm: SettingsViewModel) {
    var pinDialog by remember { mutableStateOf(false) }
    Pane("Parental controls") {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                ShButton(if (s.current?.hasPin == true) "Change PIN" else "Set PIN", { pinDialog = true })
                if (s.current?.hasPin == true) ShButton("Remove PIN", { vm.setPin(null) }, style = ButtonStyle.Danger)
            }
        }
        item { Text("Hidden channel groups (hidden for everyone; per-rating locks are configured on the server)", color = LocalThemeTokens.current.textMuted, style = MaterialTheme.typography.bodySmall) }
        items(s.groups, key = { it.id }) { g ->
            ShChip("${g.name} · ${g.channelCount}" + if (g.hidden) " · hidden" else "", g.hidden, { vm.toggleGroupHidden(g) })
        }
    }
    if (pinDialog) PinDialog("New 4-digit PIN", onSubmit = { vm.setPin(it); pinDialog = false }, onDismiss = { pinDialog = false })
}

@Composable
private fun PlaybackPane(s: SettingsState, vm: SettingsViewModel) = Pane("Playback & server") {
    val st = s.serverSettings
    item { Text("Server: ${s.serverUrl ?: "—"}", color = LocalThemeTokens.current.textMuted, style = MaterialTheme.typography.bodyMedium) }
    item {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.focusRestorer()) {
            Text("Proxy mode", color = LocalThemeTokens.current.text, modifier = Modifier.width(160.dp).align(Alignment.CenterVertically))
            listOf("auto", "always", "never").forEach { m -> ShChip(m, st?.proxyMode == m, { vm.patchServer(Settings(proxyMode = m)) }) }
        }
    }
    item {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.focusRestorer()) {
            Text("EPG refresh", color = LocalThemeTokens.current.text, modifier = Modifier.width(160.dp).align(Alignment.CenterVertically))
            listOf(6, 12, 24).forEach { h -> ShChip("${h} h", st?.epgRefreshHours == h, { vm.patchServer(Settings(epgRefreshHours = h)) }) }
        }
    }
    item {
        Text(
            "Audio passthrough/decoding: AC-3/E-AC-3 play via the TV's decoders. Devices without them need the FFmpeg extension build (see README).",
            color = LocalThemeTokens.current.textMuted, style = MaterialTheme.typography.bodySmall,
        )
    }
    item { ShButton("Sign out of this TV", vm::unpair, style = ButtonStyle.Danger) }
}

@Composable
private fun AboutPane(s: SettingsState, appVersion: String, vm: SettingsViewModel) {
    val t = LocalThemeTokens.current
    Pane("About") {
    item { Text("StreamHub for Android TV $appVersion", color = t.text, style = MaterialTheme.typography.bodyLarge) }
    item {
        Column(Modifier.background(t.surface, RoundedCornerShape(Tokens.Radius.Md)).padding(16.dp)) {
            // TMDB + JustWatch attribution (LEGAL.md). Logos are not bundled (engineering rule); text attribution shown.
            Text("TMDB", color = t.text, style = MaterialTheme.typography.titleSmall)
            Text("This product uses the TMDB API but is not endorsed or certified by TMDB.", color = t.text, style = MaterialTheme.typography.bodyMedium)
            Text("JustWatch", color = t.text, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 12.dp))
            Text("Availability data by JustWatch", color = t.text, style = MaterialTheme.typography.bodyMedium)
        }
    }
    item { Text("Legal notice (v$DISCLAIMER_VERSION)", color = t.text, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 12.dp)) }
    item {
        // Focusable so D-pad users can scroll through the full notice.
        FocusSurface(onClick = {}, modifier = Modifier.fillMaxWidth(), scaleOnFocus = false, glow = false) {
            Text(DISCLAIMER_TEXT, color = t.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(16.dp))
        }
    }
    item { Text("Open-source licences: Media3, Ktor, Coil, ZXing, Hilt, AndroidX (Apache 2.0).", color = t.textMuted, style = MaterialTheme.typography.bodySmall) }
    }
}

@Composable
private fun PinDialog(title: String, onSubmit: (String) -> Unit, onDismiss: () -> Unit) {
    val t = LocalThemeTokens.current
    var pin by remember { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        Column(Modifier.background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(28.dp).width(380.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = t.text)
            ShTextField(pin, { v -> pin = v.filter(Char::isDigit).take(8) }, "PIN", Modifier.fillMaxWidth().focusRequester(focus), password = true, keyboardType = KeyboardType.NumberPassword, onImeAction = { onSubmit(pin) })
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                ShButton("OK", { onSubmit(pin) }, style = ButtonStyle.Primary, enabled = pin.length >= 4)
                ShButton("Cancel", onDismiss)
            }
        }
    }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
}
