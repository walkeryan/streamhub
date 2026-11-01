package com.streamhub.tv.feature.onboarding

import androidx.activity.compose.BackHandler
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.DisclaimerGate
import com.streamhub.tv.core.ui.components.QrCode
import com.streamhub.tv.core.ui.components.SafeArea
import com.streamhub.tv.core.ui.components.ScreenBackground
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShTextField
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.delay

@Composable
fun OnboardingScreen(onDone: () -> Unit, vm: OnboardingViewModel = hiltViewModel()) {
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

@Composable
private fun ServerStep(s: OnboardingState, onInput: (String) -> Unit, onSubmit: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    Column(Modifier.fillMaxSize().padding(SafeArea), verticalArrangement = Arrangement.Center) {
        Text("Welcome to StreamHub", style = MaterialTheme.typography.displaySmall, color = t.text)
        Text(
            "Enter the address of your StreamHub server (shown on its web page, e.g. 192.168.1.10:8787).",
            style = MaterialTheme.typography.bodyLarge, color = t.textMuted, modifier = Modifier.padding(top = 8.dp, bottom = 24.dp).width(640.dp),
        )
        ShTextField(
            value = s.serverInput,
            onValueChange = onInput,
            label = "Server address",
            placeholder = "192.168.1.10:8787",
            keyboardType = KeyboardType.Uri,
            imeAction = ImeAction.Go,
            onImeAction = onSubmit,
            modifier = Modifier.width(520.dp).focusRequester(focus),
        )
        s.error?.let { Text(it, color = t.danger, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 12.dp)) }
        ShButton(if (s.checking) "Connecting…" else "Continue", onSubmit, Modifier.padding(top = 24.dp), style = ButtonStyle.Primary, enabled = !s.checking)
    }
}

@Composable
private fun PairingStep(s: OnboardingState, onRetry: () -> Unit, onChangeServer: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    Row(Modifier.fillMaxSize().padding(SafeArea), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(48.dp)) {
        Column(Modifier.weight(1f)) {
            Text("Pair this TV", style = MaterialTheme.typography.displaySmall, color = t.text)
            Text(
                "On your phone or computer, sign in with the authorized Google account and enter this code — or scan the QR code. If owner protection is enabled, approve with the existing owner PIN on your phone.",
                style = MaterialTheme.typography.bodyLarge, color = t.textMuted, modifier = Modifier.padding(top = 8.dp, bottom = 24.dp),
            )
            val code = s.deviceCode?.userCode
            Text(
                code?.chunked(3)?.joinToString(" ") ?: "······",
                style = MaterialTheme.typography.displayLarge.copy(fontFamily = FontFamily.Monospace, fontSize = 64.sp, letterSpacing = 6.sp),
                color = t.accent,
            )
            s.pairUrl?.let { Text(it.substringBefore("?"), style = MaterialTheme.typography.bodyMedium, color = t.textMuted, modifier = Modifier.padding(top = 12.dp)) }
            Text("Waiting for owner approval…", style = MaterialTheme.typography.bodyMedium, color = t.textMuted, modifier = Modifier.padding(top = 4.dp))
            s.error?.let { Text(it, color = t.danger, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 12.dp)) }
            Row(Modifier.padding(top = 24.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                ShButton("New code", onRetry, Modifier.focusRequester(focus))
                ShButton("Change server", onChangeServer)
            }
        }
        s.pairUrl?.let { QrCode(it, size = 260.dp) }
    }
}
