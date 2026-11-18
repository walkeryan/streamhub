package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.nativeKeyCode
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.input.PinPadState
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.glass

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
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        PinPadCard(title, hint, error, lockedSeconds, busy, onSubmit, onDismiss, attempt)
    }
}

/**
 * The pad itself, in glass: what is asked and the dots on the left, the keys with OK and Cancel
 * under them on the right (so Down from the last row still reaches OK). [initial] is for previews.
 */
@Composable
internal fun PinPadCard(
    title: String,
    hint: String,
    error: String?,
    lockedSeconds: Int,
    busy: Boolean,
    onSubmit: (String) -> Unit,
    onDismiss: () -> Unit,
    attempt: Int = 0,
    initial: PinPadState = PinPadState(),
) {
    val t = LocalThemeTokens.current
    val g = glass
    var pad by remember(attempt) { mutableStateOf(initial) }
    val accepting = !busy && lockedSeconds <= 0
    val first = remember { FocusRequester() }
    fun submit() { if (accepting) pad.submit()?.let { pin -> pad = PinPadState(); onSubmit(pin) } }

    Row(
        Modifier
            .width(780.dp)
            // The remote's digit keys (and Delete) type into the pad from any focused button.
            .onPreviewKeyEvent { e ->
                val next = pad.onKey(e.key.nativeKeyCode) ?: return@onPreviewKeyEvent false
                if (e.type == KeyEventType.KeyDown && accepting) pad = next
                true
            },
    ) {
        GlassPanel(Modifier.fillMaxWidth(), padding = PaddingValues(32.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(36.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    DialogTitle(title)
                    DialogText(hint)
                    Row(
                        Modifier.padding(vertical = 10.dp).semantics { contentDescription = "${pad.length} digits entered" },
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        repeat(PinPadState.MAX_DIGITS) { i ->
                            val filled = i < pad.length
                            Box(
                                Modifier.size(30.dp, 40.dp).clip(RoundedCornerShape(9.dp))
                                    .background(Brush.verticalGradient(listOf(g.fillTop, g.fillBottom)))
                                    .border(if (filled) 2.dp else 1.dp, if (filled) t.accent else g.line, RoundedCornerShape(9.dp)),
                                contentAlignment = Alignment.Center,
                            ) { if (filled) Box(Modifier.size(12.dp).background(g.text, CircleShape)) }
                        }
                    }
                    val (status, bad) = when {
                        lockedSeconds > 0 -> "Too many wrong PINs. Try again in ${lockoutCountdown(lockedSeconds)}." to true
                        busy -> "Checking…" to false
                        error != null -> error to true
                        else -> "${PinPadState.MIN_DIGITS} to ${PinPadState.MAX_DIGITS} digits. The number keys on the remote work too." to false
                    }
                    // Fixed height: nothing may jump when the line changes.
                    Box(Modifier.height(54.dp), contentAlignment = Alignment.TopStart) {
                        DialogText(status, color = if (bad) t.danger else g.muted, maxLines = 2, fontSize = 16.sp)
                    }
                }
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    listOf("123", "456", "789").forEachIndexed { r, row ->
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            row.forEachIndexed { c, digit ->
                                PadKey(digit.toString(), { if (accepting) pad = pad.append(digit - '0') }, if (r == 0 && c == 0) Modifier.focusRequester(first) else Modifier, dimmed = !accepting)
                            }
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        PadKey("Clear", { if (accepting) pad = pad.clear() }, dimmed = !accepting, small = true)
                        PadKey("0", { if (accepting) pad = pad.append(0) }, dimmed = !accepting)
                        PadKey("⌫", { if (accepting) pad = pad.backspace() }, Modifier.semantics { contentDescription = "Delete last digit" }, dimmed = !accepting)
                    }
                    Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        // Never disabled: a button that stops being focusable while it holds focus strands the D-pad.
                        ShButton("OK", ::submit, Modifier.width(PadKeyW + PadKeyW / 2 + 5.dp), style = if (accepting && pad.canSubmit) ButtonStyle.Primary else ButtonStyle.Secondary)
                        ShButton("Cancel", onDismiss, Modifier.width(PadKeyW + PadKeyW / 2 + 5.dp))
                    }
                }
            }
        }
    }
    LaunchedEffect(Unit) { withFrameNanos { }; runCatching { first.requestFocus() } }
}

private val PadKeyW = 96.dp

/**
 * One key. Focus is the owner's keypad highlight: the key turns solid light with dark ink inside
 * the accent ring, so which digit OK will type is plain from across the room.
 */
@Composable
private fun PadKey(label: String, onClick: () -> Unit, modifier: Modifier = Modifier, dimmed: Boolean = false, small: Boolean = false) {
    val t = LocalThemeTokens.current
    val g = glass
    val source = remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val shape = RoundedCornerShape(12.dp)
    Box(
        modifier
            .size(PadKeyW, 54.dp)
            .then(if (focused) Modifier.shadow(12.dp, shape, ambientColor = t.accent, spotColor = t.accent) else Modifier)
            .clip(shape)
            .background(if (focused) SolidColor(g.focusFill) else Brush.verticalGradient(listOf(g.fillTop, g.fillBottom)))
            .border(if (focused) 3.dp else 1.dp, if (focused) t.accent else g.line, shape)
            .clickable(interactionSource = source, indication = null, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        val color = (if (focused) g.focusInk else g.text).let { if (dimmed) it.copy(alpha = 0.5f) else it }
        Text(label, color = color, fontSize = if (small) 17.sp else 23.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
    }
}
