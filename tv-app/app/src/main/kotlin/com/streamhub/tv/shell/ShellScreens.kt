package com.streamhub.tv.shell

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.DialogText
import com.streamhub.tv.core.ui.components.DialogTitle
import com.streamhub.tv.core.ui.components.GlassIcons
import com.streamhub.tv.core.ui.components.GlassPanel
import com.streamhub.tv.core.ui.components.GroupLabel
import com.streamhub.tv.core.ui.components.SafeArea
import com.streamhub.tv.core.ui.components.ScreenBackground
import com.streamhub.tv.core.ui.components.ScreenLead
import com.streamhub.tv.core.ui.components.ScreenTitle
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.Wordmark
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.glass

/** The web app could not be loaded: the address, Retry, a way to another server, and the TV's own settings. */
@Composable
fun UnreachableScreen(serverUrl: String?, onRetry: () -> Unit, onChangeServer: () -> Unit, onSettings: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    ScreenBackground {
        Box(Modifier.fillMaxSize().padding(SafeArea)) {
            Wordmark(Modifier.align(Alignment.TopStart))
            Column(Modifier.align(Alignment.CenterStart).width(760.dp)) {
                IconBadge(GlassIcons.Server, t.danger)
                ScreenTitle("Cannot reach your server", Modifier.padding(top = 20.dp))
                ScreenLead(
                    "StreamHub could not load ${serverUrl ?: "your server"}. Check that the server is running and that this TV is on the same network.",
                    Modifier.padding(top = 10.dp, bottom = 28.dp),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    ShButton("Retry", onRetry, Modifier.focusRequester(focus), icon = GlassIcons.Retry, style = ButtonStyle.Primary)
                    ShButton("Change server", onChangeServer, icon = GlassIcons.Server)
                    ShButton("TV settings", onSettings, icon = GlassIcons.Settings)
                }
            }
        }
    }
}

/** A tinted disc with an icon in a signal colour: what a screen or dialog is about, above its heading. */
@Composable
private fun IconBadge(icon: ImageVector, tint: Color) {
    Box(
        Modifier.size(64.dp).clip(CircleShape).background(tint.copy(alpha = 0.16f)).border(1.dp, tint.copy(alpha = 0.55f), CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Image(icon, null, Modifier.size(30.dp), colorFilter = ColorFilter.tint(tint))
    }
}

/** Server address, sign out of this TV, version and updates: the settings that belong to the TV itself. */
@Composable
fun TvSettingsScreen(appVersion: String, onBack: () -> Unit, onChangeServer: () -> Unit, onSignOut: () -> Unit, vm: TvSettingsViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    BackHandler(onBack = onBack)
    TvSettingsContent(s, appVersion, onBack, onChangeServer, onSignOut, onInstall = vm::installUpdate, onCheck = vm::checkForUpdate)
}

/**
 * The TV settings, stateless: the heading and Back on the left, two glass panels on the right (the
 * server this TV uses, and the app's version and updates). Focus starts on Back.
 */
@Composable
internal fun TvSettingsContent(
    s: TvSettingsState,
    appVersion: String,
    onBack: () -> Unit,
    onChangeServer: () -> Unit,
    onSignOut: () -> Unit,
    onInstall: () -> Unit,
    onCheck: () -> Unit,
) {
    val g = glass
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    ScreenBackground {
        Row(Modifier.fillMaxSize().padding(SafeArea), horizontalArrangement = Arrangement.spacedBy(40.dp)) {
            Column(Modifier.width(270.dp)) {
                Wordmark()
                ScreenTitle("TV settings", Modifier.padding(top = 44.dp))
                DialogText("What belongs to this TV. Everything else is in the app's own Settings.", Modifier.padding(top = 10.dp, bottom = 28.dp))
                ShButton("Back", onBack, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
            }
            Column(Modifier.weight(1f).padding(top = 6.dp), verticalArrangement = Arrangement.spacedBy(18.dp)) {
                SettingsPanel(GlassIcons.Server, "Server") {
                    Text(s.serverUrl ?: "No server set", color = g.text, fontSize = 21.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                        ShButton("Change server", onChangeServer)
                        ShButton("Sign out of this TV", onSignOut, icon = GlassIcons.Leave, style = ButtonStyle.Danger)
                    }
                }
                SettingsPanel(GlassIcons.Download, "App version") {
                    Text("StreamHub for Android TV $appVersion", color = g.text, fontSize = 21.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    s.updateStatus?.let { DialogText(it, maxLines = 2, fontSize = 16.sp) }
                    Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                        val update = s.update
                        if (update != null) ShButton("Install ${update.versionName}", onInstall, enabled = !s.updateBusy, icon = GlassIcons.Download, style = ButtonStyle.Primary)
                        ShButton(if (s.checking) "Checking…" else "Check for updates", onCheck, enabled = !s.checking && !s.updateBusy)
                    }
                }
            }
        }
    }
}

@Composable
private fun SettingsPanel(icon: ImageVector, label: String, content: @Composable () -> Unit) {
    val g = glass
    GlassPanel(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp), padding = PaddingValues(horizontal = 26.dp, vertical = 22.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Image(icon, null, Modifier.size(18.dp), colorFilter = ColorFilter.tint(g.dim))
            GroupLabel(label)
        }
        content()
    }
}

/** The shell's own "a newer StreamHub is available" (see [UpdatePromptViewModel]). Back is "Later". */
@Composable
fun UpdatePromptDialog(s: UpdatePromptState, appVersion: String, onInstall: () -> Unit, onLater: () -> Unit) {
    if (s.offer == null) return
    Dialog(onDismissRequest = { if (!s.busy) onLater() }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        UpdatePromptCard(s, appVersion, onInstall, onLater)
    }
}

@Composable
internal fun UpdatePromptCard(s: UpdatePromptState, appVersion: String, onInstall: () -> Unit, onLater: () -> Unit) {
    val offer = s.offer ?: return
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    GlassPanel(Modifier.width(600.dp)) {
        IconBadge(GlassIcons.Download, t.accent)
        DialogTitle("Update available", Modifier.padding(top = 6.dp))
        DialogText("StreamHub ${offer.versionName} is ready to install. This TV has $appVersion.")
        s.status?.let { DialogText(it, fontSize = 16.sp) }
        Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            ShButton(if (s.busy) "Installing…" else "Install now", onInstall, Modifier.focusRequester(focus), enabled = !s.busy, icon = GlassIcons.Download, style = ButtonStyle.Primary)
            ShButton("Later", onLater, enabled = !s.busy)
        }
    }
    LaunchedEffect(offer) { withFrameNanos { }; runCatching { focus.requestFocus() } }
}
