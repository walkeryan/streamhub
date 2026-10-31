package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** Last-resort hand-off: app not installed → scan to open on phone/web. */
@Composable
fun HandoffQrDialog(webUrl: String, serviceName: String?, onDismiss: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(32.dp).width(520.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text("${serviceName ?: "This app"} isn't installed on this TV", style = MaterialTheme.typography.titleMedium, color = t.text)
            Text("Scan to open it on your phone, or install the app from the Play Store.", style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
            QrCode(webUrl)
            Text(webUrl, style = MaterialTheme.typography.labelMedium, color = t.textMuted, maxLines = 2)
            ShButton("Close", onDismiss, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
        }
    }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
}

