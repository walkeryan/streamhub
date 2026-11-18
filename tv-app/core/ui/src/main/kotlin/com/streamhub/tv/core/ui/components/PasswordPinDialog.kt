package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.glass

/** The longest password the hub keeps; anything longer could never be right. */
private const val PASSWORD_MAX = 64

/**
 * A PIN chosen as a password: one masked field, typed on the system keyboard (OK on the focused field
 * opens it), sent with the keyboard's Done key or Continue. Nothing is trimmed: the hub compares the
 * password exactly as it was chosen. [error], [lockedSeconds], [busy] and [attempt] behave as in
 * [PinPadDialog]; a new [attempt] empties the field.
 */
@Composable
fun PasswordPinDialog(
    title: String,
    hint: String,
    error: String?,
    lockedSeconds: Int,
    busy: Boolean,
    onSubmit: (String) -> Unit,
    onDismiss: () -> Unit,
    attempt: Int = 0,
) {
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        PasswordPinCard(title, hint, error, lockedSeconds, busy, onSubmit, onDismiss, attempt)
    }
}

/** The password's card, in glass. [initial] is for previews. */
@Composable
internal fun PasswordPinCard(
    title: String,
    hint: String,
    error: String?,
    lockedSeconds: Int,
    busy: Boolean,
    onSubmit: (String) -> Unit,
    onDismiss: () -> Unit,
    attempt: Int = 0,
    initial: String = "",
) {
    val t = LocalThemeTokens.current
    var value by remember(attempt) { mutableStateOf(initial) }
    val accepting = !busy && lockedSeconds <= 0
    val field = remember { FocusRequester() }
    fun submit() { if (accepting && value.isNotEmpty()) onSubmit(value) }

    GlassPanel(Modifier.width(560.dp)) {
        DialogTitle(title)
        DialogText(hint)
        ShTextField(
            value = value,
            onValueChange = { if (accepting) value = it.take(PASSWORD_MAX) },
            label = "Password",
            modifier = Modifier.padding(top = 6.dp).fillMaxWidth().focusRequester(field),
            password = true,
            keyboardType = KeyboardType.Password,
            imeAction = ImeAction.Done,
            onImeAction = ::submit,
        )
        val (status, bad) = when {
            lockedSeconds > 0 -> "Too many wrong passwords. Try again in ${lockoutCountdown(lockedSeconds)}." to true
            busy -> "Checking…" to false
            error != null -> error to true
            else -> "Press OK to type it on the keyboard." to false
        }
        // Fixed height: the buttons must not jump when the line changes.
        Box(Modifier.height(48.dp), contentAlignment = Alignment.CenterStart) {
            DialogText(status, color = if (bad) t.danger else glass.muted, maxLines = 2, fontSize = 16.sp)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            // Never disabled: a button that stops being focusable while it holds focus strands the D-pad.
            ShButton("Continue", ::submit, style = if (accepting && value.isNotEmpty()) ButtonStyle.Primary else ButtonStyle.Secondary)
            ShButton("Cancel", onDismiss)
        }
    }
    LaunchedEffect(Unit) { withFrameNanos { }; runCatching { field.requestFocus() } }
}
