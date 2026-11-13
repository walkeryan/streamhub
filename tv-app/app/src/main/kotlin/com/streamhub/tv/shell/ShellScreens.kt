package com.streamhub.tv.shell

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.SafeArea
import com.streamhub.tv.core.ui.components.ScreenBackground
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.theme.LocalThemeTokens

/** The web app could not be loaded: the address, Retry, a way to another server, and the TV's own settings. */
@Composable
fun UnreachableScreen(serverUrl: String?, onRetry: () -> Unit, onChangeServer: () -> Unit, onSettings: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    ScreenBackground {
        Column(Modifier.fillMaxSize().padding(SafeArea), verticalArrangement = Arrangement.Center) {
            Text("Cannot reach your server", style = MaterialTheme.typography.displaySmall, color = t.text)
            Text(
                "StreamHub could not load ${serverUrl ?: "your server"}. Check that the server is running and that this TV is on the same network.",
                style = MaterialTheme.typography.bodyLarge, color = t.textMuted, modifier = Modifier.padding(top = 8.dp, bottom = 24.dp).width(720.dp),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                ShButton("Retry", onRetry, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
                ShButton("Change server", onChangeServer)
                ShButton("TV settings", onSettings)
            }
        }
    }
}

/** Server address, sign out of this TV, version and updates: the settings that belong to the TV itself. */
@Composable
fun TvSettingsScreen(appVersion: String, onBack: () -> Unit, onChangeServer: () -> Unit, onSignOut: () -> Unit, vm: TvSettingsViewModel = hiltViewModel()) {
    val t = LocalThemeTokens.current
    val s by vm.state.collectAsStateWithLifecycle()
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    BackHandler(onBack = onBack)
    ScreenBackground {
        Column(Modifier.fillMaxSize().padding(SafeArea), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text("TV settings", style = MaterialTheme.typography.displaySmall, color = t.text)
            Text("Server", style = MaterialTheme.typography.titleMedium, color = t.text)
            Text(s.serverUrl ?: "No server set", style = MaterialTheme.typography.bodyLarge, color = t.textMuted)
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                ShButton("Back", onBack, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
                ShButton("Change server", onChangeServer)
                ShButton("Sign out of this TV", onSignOut, style = ButtonStyle.Danger)
            }
            Text("App version", style = MaterialTheme.typography.titleMedium, color = t.text, modifier = Modifier.padding(top = 16.dp))
            Text("StreamHub for Android TV $appVersion", style = MaterialTheme.typography.bodyLarge, color = t.textMuted)
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                val update = s.update
                if (update != null) ShButton("Install ${update.versionName}", vm::installUpdate, enabled = !s.updateBusy, style = ButtonStyle.Primary)
                ShButton(if (s.checking) "Checking…" else "Check for updates", vm::checkForUpdate, enabled = !s.checking && !s.updateBusy)
            }
            s.updateStatus?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = t.textMuted, modifier = Modifier.width(720.dp)) }
        }
    }
}
