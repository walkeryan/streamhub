package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
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
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.nativeKeyCode
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.input.PinPadState
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** "0:45": what is left of a lockout. */
fun lockoutCountdown(seconds: Int): String = "%d:%02d".format(seconds.coerceAtLeast(0) / 60, seconds.coerceAtLeast(0) % 60)

/**
 * PIN entry without a keyboard: a number pad the D-pad walks, and the remote's own digit keys type
 * straight into it wherever focus is. Digits are shown as dots. [error] says what was wrong with the
 * last attempt; while [lockedSeconds] is above zero nothing is accepted and the time left is shown.
 * [attempt] changes after every answer from the server, which empties the pad for the next try.
 */
@Composable
fun PinPadDialog(
    title: String,
    hint: String,
    error: String?,
    lockedSeconds: Int,
    busy: Boolean,
    onSubmit: (String) -> Unit,
    onDismiss: () -> Unit,
    attempt: Int = 0,
) {
    val t = LocalThemeTokens.current
    var pad by remember(attempt) { mutableStateOf(PinPadState()) }
    val accepting = !busy && lockedSeconds <= 0
    val first = remember { FocusRequester() }
    fun submit() { if (accepting) pad.submit()?.let { pin -> pad = PinPadState(); onSubmit(pin) } }

    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .width(400.dp)
                .background(t.surface, RoundedCornerShape(Tokens.Radius.Lg))
                .padding(28.dp)
                // The remote's digit keys (and Delete) type into the pad from any focused button.
                .onPreviewKeyEvent { e ->
                    val next = pad.onKey(e.key.nativeKeyCode) ?: return@onPreviewKeyEvent false
                    if (e.type == KeyEventType.KeyDown && accepting) pad = next
                    true
                },
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleLarge, color = t.text, maxLines = 2)
            Text(hint, style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
            Row(
                Modifier.padding(vertical = 4.dp).semantics { contentDescription = "${pad.length} digits entered" },
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                repeat(PinPadState.MAX_DIGITS) { i ->
                    Box(
                        Modifier.size(28.dp, 36.dp).background(t.surfaceAlt, RoundedCornerShape(Tokens.Radius.Sm)),
                        contentAlignment = Alignment.Center,
                    ) { if (i < pad.length) Text("•", style = MaterialTheme.typography.titleLarge, color = t.text) }
                }
            }
            val (status, bad) = when {
                lockedSeconds > 0 -> "Too many wrong PINs. Try again in ${lockoutCountdown(lockedSeconds)}." to true
                busy -> "Checking…" to false
                error != null -> error to true
                else -> "${PinPadState.MIN_DIGITS} to ${PinPadState.MAX_DIGITS} digits. The number keys on the remote work too." to false
            }
            // Fixed height: the pad must not jump when the line changes.
            Box(Modifier.height(44.dp), contentAlignment = Alignment.Center) {
                Text(status, style = MaterialTheme.typography.bodyMedium, color = if (bad) t.danger else t.textMuted, maxLines = 2)
            }
            listOf("123", "456", "789").forEachIndexed { r, row ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    row.forEachIndexed { c, digit ->
                        PadKey(digit.toString(), { if (accepting) pad = pad.append(digit - '0') }, if (r == 0 && c == 0) Modifier.focusRequester(first) else Modifier, dimmed = !accepting)
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                PadKey("Clear", { if (accepting) pad = pad.clear() }, dimmed = !accepting)
                PadKey("0", { if (accepting) pad = pad.append(0) }, dimmed = !accepting)
                PadKey("⌫", { if (accepting) pad = pad.backspace() }, Modifier.semantics { contentDescription = "Delete last digit" }, dimmed = !accepting)
            }
            Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                // Never disabled: a button that stops being focusable while it holds focus strands the D-pad.
                ShButton("OK", ::submit, style = if (accepting && pad.canSubmit) ButtonStyle.Primary else ButtonStyle.Secondary)
                ShButton("Cancel", onDismiss)
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { first.requestFocus() } }
    }
}

@Composable
private fun PadKey(label: String, onClick: () -> Unit, modifier: Modifier = Modifier, dimmed: Boolean = false) {
    val t = LocalThemeTokens.current
    FocusSurface(
        onClick = onClick,
        modifier = modifier.size(88.dp, 48.dp),
        shape = RoundedCornerShape(Tokens.Radius.Md),
        containerColor = t.surfaceAlt,
        focusedContainerColor = t.text,
        scaleOnFocus = false,
        glow = false,
    ) { focused ->
        val color = (if (focused) t.bg else t.text).let { if (dimmed) it.copy(alpha = 0.5f) else it }
        Text(label, Modifier.align(Alignment.Center), style = MaterialTheme.typography.titleMedium, color = color, maxLines = 1)
    }
}
