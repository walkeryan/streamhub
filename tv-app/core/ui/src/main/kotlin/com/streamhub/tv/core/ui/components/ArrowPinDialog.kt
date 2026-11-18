package com.streamhub.tv.core.ui.components

import android.view.KeyEvent as AndroidKeyEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.streamhub.tv.core.ui.input.Arrow
import com.streamhub.tv.core.ui.input.ArrowPinState
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.glass

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
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        ArrowPinCard(title, hint, length, error, lockedSeconds, busy, onSubmit, attempt)
    }
}

/** The pattern's card, in glass; Back reaches the dialog round it. [initial] is for previews. */
@Composable
internal fun ArrowPinCard(
    title: String,
    hint: String,
    length: Int,
    error: String?,
    lockedSeconds: Int,
    busy: Boolean,
    onSubmit: (String) -> Unit,
    attempt: Int = 0,
    initial: ArrowPinState = ArrowPinState(length),
) {
    val t = LocalThemeTokens.current
    val g = glass
    var pattern by remember(attempt, length) { mutableStateOf(initial) }
    val accepting = !busy && lockedSeconds <= 0
    val target = remember { FocusRequester() }

    GlassPanel(
        Modifier
            .width(560.dp)
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
        DialogTitle(title)
        DialogText(hint)
        // The one focusable thing: the key events above need a focused node inside this window to arrive at.
        Row(
            Modifier
                .padding(vertical = 16.dp)
                .focusRequester(target)
                .focusable()
                .semantics { contentDescription = "${pattern.entered} of ${pattern.length} arrows entered" },
            horizontalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            repeat(pattern.length) { i ->
                val filled = i < pattern.entered
                Box(
                    Modifier.size(28.dp).clip(CircleShape)
                        .background(if (filled) Brush.verticalGradient(listOf(g.accentTop, g.accentBottom)) else Brush.verticalGradient(listOf(g.fillTop, g.fillBottom)))
                        .then(if (filled) Modifier else Modifier.border(1.5.dp, g.line, CircleShape)),
                )
            }
        }
        val (status, bad) = when {
            lockedSeconds > 0 -> "Too many wrong patterns. Try again in ${lockoutCountdown(lockedSeconds)}." to true
            busy -> "Checking…" to false
            error != null -> error to true
            else -> "Press ${Arrow.entries.joinToString(" ") { it.glyph }} on the remote, ${pattern.length} times. OK starts again, Back cancels." to false
        }
        // Fixed height: the dots must not jump when the line changes.
        Box(Modifier.height(54.dp), contentAlignment = Alignment.Center) {
            DialogText(status, color = if (bad) t.danger else g.muted, maxLines = 2, fontSize = 16.sp, textAlign = TextAlign.Center)
        }
    }
    LaunchedEffect(Unit) { withFrameNanos { }; runCatching { target.requestFocus() } }
}
