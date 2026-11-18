package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.streamhub.tv.core.data.api.UserError

/**
 * A failure the viewer can answer with the remote: focus starts on "Try again" when a retry can
 * help, otherwise on the dismiss button. Back dismisses. A toast could do neither and vanished
 * before it could be read from the couch.
 */
@Composable
fun ErrorDialog(error: UserError, onRetry: () -> Unit, onDismiss: () -> Unit, dismissLabel: String = "OK") {
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        ErrorCard(error, onRetry, onDismiss, dismissLabel)
    }
}

@Composable
internal fun ErrorCard(error: UserError, onRetry: () -> Unit, onDismiss: () -> Unit, dismissLabel: String = "OK") {
    val focus = remember { FocusRequester() }
    GlassPanel(Modifier.width(580.dp)) {
        DialogTitle(error.title)
        DialogText(error.body)
        Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            if (error.canRetry) ShButton("Try again", onRetry, Modifier.focusRequester(focus), icon = GlassIcons.Retry, style = ButtonStyle.Primary)
            ShButton(dismissLabel, onDismiss, if (error.canRetry) Modifier else Modifier.focusRequester(focus))
        }
    }
    LaunchedEffect(error) { withFrameNanos { }; runCatching { focus.requestFocus() } }
}
