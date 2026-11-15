package com.streamhub.tv.core.ui.components

import android.view.KeyEvent as AndroidKeyEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
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
import com.streamhub.tv.core.ui.input.Arrow
import com.streamhub.tv.core.ui.input.ArrowPinState
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** The keys that are OK on a remote or keyboard: they start the pattern again and never send it. */
private val OK_KEYS = setOf(AndroidKeyEvent.KEYCODE_DPAD_CENTER, AndroidKeyEvent.KEYCODE_ENTER, AndroidKeyEvent.KEYCODE_NUMPAD_ENTER)

/**
 * A PIN entered as an arrow pattern of [length] D-pad presses: one dot fills per press, and the
 * last one sends it ([onSubmit] gets the keypad digits, see [Arrow]). There is nothing to focus and
 * no OK step, so the arrows can only ever mean the pattern: every arrow press is kept inside the
 * dialog, OK clears the dots, and Back cancels. Which arrows were pressed is never shown. [error],
 * [lockedSeconds], [busy] and [attempt] behave as in [PinPadDialog].
 */
@Composable
fun ArrowPinDialog(
    title: String,
    hint: String,
    length: Int,
    error: String?,
    lockedSeconds: Int,
    busy: Boolean,
    onSubmit: (String) -> Unit,
    onDismiss: () -> Unit,
    attempt: Int = 0,
) {
    val t = LocalThemeTokens.current
    var pattern by remember(attempt, length) { mutableStateOf(ArrowPinState(length)) }
    val accepting = !busy && lockedSeconds <= 0
    val target = remember { FocusRequester() }

    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .width(460.dp)
                .background(t.surface, RoundedCornerShape(Tokens.Radius.Lg))
                .padding(28.dp)
                .onPreviewKeyEvent { e ->
                    val code = e.key.nativeKeyCode
                    val arrow = Arrow.ofKey(code)
                    when {
                        arrow != null -> {
                            // A held key repeats; only a fresh press is an arrow, or one long press would fill every dot.
                            if (e.type == KeyEventType.KeyDown && e.nativeKeyEvent.repeatCount == 0 && accepting) {
                                val next = pattern.add(arrow)
                                // Unchanged once complete, so a quick extra press cannot send the pattern twice.
                                if (next !== pattern) { pattern = next; next.submit()?.let(onSubmit) }
                            }
                            // Both halves of every arrow press stop here: focus has nowhere to go and must not try.
                            true
                        }
                        code in OK_KEYS -> {
                            if (e.type == KeyEventType.KeyDown && accepting) pattern = pattern.clear()
                            true
                        }
                        // Back reaches the dialog's own dismissal.
                        else -> false
                    }
                },
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleLarge, color = t.text, maxLines = 2)
            Text(hint, style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
            // The one focusable thing: the key events above need a focused node inside this window to arrive at.
            Row(
                Modifier
                    .padding(vertical = 8.dp)
                    .focusRequester(target)
                    .focusable()
                    .semantics { contentDescription = "${pattern.entered} of ${pattern.length} arrows entered" },
                horizontalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                repeat(pattern.length) { i ->
                    val filled = i < pattern.entered
                    Box(Modifier.size(22.dp).background(if (filled) t.text else t.surfaceAlt, CircleShape))
                }
            }
            val (status, bad) = when {
                lockedSeconds > 0 -> "Too many wrong patterns. Try again in ${lockoutCountdown(lockedSeconds)}." to true
                busy -> "Checking…" to false
                error != null -> error to true
                else -> "Press ${Arrow.entries.joinToString(" ") { it.glyph }} on the remote, ${pattern.length} times. OK starts again, Back cancels." to false
            }
            // Fixed height: the dots must not jump when the line changes.
            Box(Modifier.height(48.dp), contentAlignment = Alignment.Center) {
                Text(status, style = MaterialTheme.typography.bodyMedium, color = if (bad) t.danger else t.textMuted, maxLines = 2)
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { target.requestFocus() } }
    }
}
