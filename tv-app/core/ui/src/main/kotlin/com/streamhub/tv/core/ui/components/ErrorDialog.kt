package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/**
 * A failure the viewer can answer with the remote: focus starts on "Try again" when a retry can
 * help, otherwise on the dismiss button. Back dismisses. A toast could do neither and vanished
 * before it could be read from the couch.
 */
@Composable
fun ErrorDialog(error: UserError, onRetry: () -> Unit, onDismiss: () -> Unit, dismissLabel: String = "OK") {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.width(520.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(error.title, style = MaterialTheme.typography.titleLarge, color = t.text)
            Text(error.body, style = MaterialTheme.typography.bodyLarge, color = t.textMuted)
            Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (error.canRetry) ShButton("Try again", onRetry, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
                ShButton(dismissLabel, onDismiss, if (error.canRetry) Modifier else Modifier.focusRequester(focus))
            }
        }
        LaunchedEffect(error) { withFrameNanos { }; runCatching { focus.requestFocus() } }
    }
}
