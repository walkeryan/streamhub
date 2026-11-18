package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.streamhub.tv.core.ui.theme.glass

/** Last-resort hand-off: app not installed → scan to open on phone/web. */
@Composable
fun HandoffQrDialog(webUrl: String, serviceName: String?, onDismiss: () -> Unit) {
    val focus = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        GlassPanel(Modifier.width(560.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
            DialogTitle("${serviceName ?: "This app"} isn't installed on this TV", textAlign = TextAlign.Center)
            DialogText("Scan to open it on your phone, or install the app from the Play Store.", textAlign = TextAlign.Center)
            QrTile(webUrl, Modifier.padding(vertical = 4.dp), size = 200.dp)
            DialogText(webUrl, maxLines = 2, fontSize = 14.sp, color = glass.dim, textAlign = TextAlign.Center)
            ShButton("Close", onDismiss, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
        }
    }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
}
