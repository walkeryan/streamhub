package com.streamhub.tv.feature.onboarding

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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.components.BigCode
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.DialogText
import com.streamhub.tv.core.ui.components.DisclaimerGate
import com.streamhub.tv.core.ui.components.GlassIcons
import com.streamhub.tv.core.ui.components.GlassPanel
import com.streamhub.tv.core.ui.components.GroupLabel
import com.streamhub.tv.core.ui.components.QrTile
import com.streamhub.tv.core.ui.components.SafeArea
import com.streamhub.tv.core.ui.components.ScreenBackground
import com.streamhub.tv.core.ui.components.ScreenLead
import com.streamhub.tv.core.ui.components.ScreenTitle
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShTextField
import com.streamhub.tv.core.ui.components.Wordmark
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.glass

@Composable
fun OnboardingScreen(onDone: () -> Unit, startAtServer: Boolean = false, vm: OnboardingViewModel = hiltViewModel()) {
    // "Change server" from the shell: the address first, even though one is known.
    LaunchedEffect(vm, startAtServer) { if (startAtServer) vm.startAtServer() }
    val s by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(s.step) { if (s.step == OnboardingStep.Done) onDone() }
    ScreenBackground {
        when (s.step) {
            OnboardingStep.Server -> ServerStep(s, vm::onServerInput, vm::submitServer)
            OnboardingStep.Disclaimer -> {
                val activity = LocalContext.current as? android.app.Activity
                DisclaimerGate(text = s.disclaimerText, version = s.disclaimerVersion, onAccept = vm::acceptDisclaimer, onExit = { activity?.finish() })
            }
            OnboardingStep.Pairing -> {
                BackHandler { vm.backToServer() }
                PairingStep(s, onRetry = vm::startPairing, onChangeServer = vm::backToServer)
            }
            OnboardingStep.Done -> Unit
        }
    }
}

/** The server's address: the heading and the field on the left, a glass disc with the server mark on the right. */
@Composable
internal fun ServerStep(s: OnboardingState, onInput: (String) -> Unit, onSubmit: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    Box(Modifier.fillMaxSize().padding(SafeArea)) {
        Wordmark(Modifier.align(Alignment.TopStart))
        Row(Modifier.align(Alignment.CenterStart).fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(48.dp)) {
            Column(Modifier.weight(1f)) {
                ScreenTitle("Welcome to StreamHub")
                ScreenLead(
                    "Enter the address of your StreamHub server (shown on its web page, e.g. 192.168.1.10:8787).",
                    Modifier.padding(top = 10.dp, bottom = 26.dp),
                )
                ShTextField(
                    value = s.serverInput,
                    onValueChange = onInput,
                    label = "Server address",
                    placeholder = "192.168.1.10:8787",
                    keyboardType = KeyboardType.Uri,
                    imeAction = ImeAction.Go,
                    onImeAction = onSubmit,
                    modifier = Modifier.width(540.dp).focusRequester(focus),
                )
                // Fixed height: the button must not jump when an error comes and goes.
                Box(Modifier.height(40.dp), contentAlignment = Alignment.CenterStart) {
                    s.error?.let { DialogText(it, color = t.danger, maxLines = 1, fontSize = 16.sp) }
                }
                ShButton(if (s.checking) "Connecting…" else "Continue", onSubmit, style = ButtonStyle.Primary, enabled = !s.checking)
            }
            GlassDisc(GlassIcons.Server)
        }
    }
}

/** A large glass circle with an icon in it: the decoration beside a step's form. */
@Composable
private fun GlassDisc(icon: androidx.compose.ui.graphics.vector.ImageVector) {
    val t = LocalThemeTokens.current
    val g = glass
    Box(
        Modifier.size(200.dp).clip(CircleShape)
            .background(Brush.verticalGradient(listOf(g.fillTop, g.fillBottom)))
            .border(1.dp, g.line, CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Image(icon, null, Modifier.size(84.dp), colorFilter = ColorFilter.tint(t.accent))
    }
}

/** The device code: the code itself big in the accent with what to do around it, and the QR code to scan. */
@Composable
internal fun PairingStep(s: OnboardingState, onRetry: () -> Unit, onChangeServer: () -> Unit) {
    val t = LocalThemeTokens.current
    val g = glass
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    Box(Modifier.fillMaxSize().padding(SafeArea)) {
        Wordmark(Modifier.align(Alignment.TopStart))
        Row(Modifier.align(Alignment.CenterStart).fillMaxWidth().padding(top = 24.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(44.dp)) {
            Column(Modifier.weight(1f)) {
                ScreenTitle("Pair this TV")
                DialogText(
                    "On your phone or computer, sign in with the authorized Google account and enter this code — or scan the QR code. If owner protection is enabled, approve with the existing owner PIN on your phone.",
                    Modifier.padding(top = 8.dp, bottom = 18.dp),
                )
                GlassPanel(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp), padding = PaddingValues(horizontal = 26.dp, vertical = 18.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    GroupLabel("Your code")
                    val code = s.deviceCode?.userCode
                    BigCode(code?.chunked(3)?.joinToString(" ") ?: "······", fontSize = 58.sp)
                    s.pairUrl?.let { Text(it.substringBefore("?"), color = g.text, fontSize = 16.sp, maxLines = 1) }
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Box(Modifier.size(8.dp).background(t.accent, CircleShape))
                        Text("Waiting for owner approval…", color = g.muted, fontSize = 15.sp)
                    }
                }
                Box(Modifier.height(36.dp), contentAlignment = Alignment.CenterStart) {
                    s.error?.let { DialogText(it, color = t.danger, maxLines = 1, fontSize = 16.sp) }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    ShButton("New code", onRetry, Modifier.focusRequester(focus), icon = GlassIcons.Retry)
                    ShButton("Change server", onChangeServer, icon = GlassIcons.Server)
                }
            }
            s.pairUrl?.let { url ->
                Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    QrTile(url, size = 240.dp)
                    Text("Scan with your phone", color = g.muted, fontSize = 15.sp)
                }
            }
        }
    }
}
