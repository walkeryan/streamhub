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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.repo.RoomSubject
import com.streamhub.tv.core.data.repo.WatchTogether
import com.streamhub.tv.core.data.room.RoomCode
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** What happens once Start is pressed, by what the room is opened on. */
internal fun watchTogetherHint(subject: RoomSubject): String = when (subject) {
    RoomSubject.Title -> "Start when you're ready. It waits, paused, until someone presses Play."
    RoomSubject.Series -> "Start when you're ready. The room opens on the next episode to watch and waits, paused, until someone presses Play."
    RoomSubject.Channel -> "Start when you're ready. A live room has no position: everyone watches the channel as it airs, and Play and Pause apply to everyone."
}

/**
 * The room's code, large enough to read across the room, and a QR code of `<server>/room/<code>` for
 * phones and computers. Start joins the room and opens the player; Back or Cancel closes the room.
 * Shared by a title's page, an episode's actions and Live TV.
 */
@Composable
fun WatchTogetherDialog(state: WatchTogether, onStart: () -> Unit, onRetry: () -> Unit, onCancel: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    val phase = state.phase
    Dialog(onDismissRequest = onCancel, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Row(
            Modifier.width(820.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(32.dp),
            horizontalArrangement = Arrangement.spacedBy(32.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Watch together", style = MaterialTheme.typography.titleLarge, color = t.text)
                Text(state.title, style = MaterialTheme.typography.bodyLarge, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                when (phase) {
                    WatchTogether.Phase.Creating -> {
                        Text("Opening a room…", style = MaterialTheme.typography.bodyLarge, color = t.text)
                        ShButton("Cancel", onCancel, Modifier.focusRequester(focus))
                    }
                    is WatchTogether.Phase.Failed -> {
                        Text(phase.error.title, style = MaterialTheme.typography.titleMedium, color = t.danger)
                        Text(phase.error.body, style = MaterialTheme.typography.bodyLarge, color = t.textMuted)
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            if (phase.error.canRetry) ShButton("Try again", onRetry, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
                            ShButton("Close", onCancel, if (phase.error.canRetry) Modifier else Modifier.focusRequester(focus))
                        }
                    }
                    is WatchTogether.Phase.Ready -> {
                        Text("Room code", style = MaterialTheme.typography.labelLarge, color = t.textMuted)
                        Text(
                            RoomCode.display(phase.room.code),
                            style = MaterialTheme.typography.displayLarge.copy(fontFamily = FontFamily.Monospace, fontSize = 64.sp, letterSpacing = 6.sp),
                            color = t.accent,
                        )
                        Text(
                            "On another TV: open Join a room from the menu and enter this code. On a phone or computer: scan the QR code" +
                                (phase.shareUrl?.let { " or open $it" } ?: "") + ".",
                            style = MaterialTheme.typography.bodyMedium, color = t.textMuted,
                        )
                        val error = phase.error
                        when {
                            phase.needsAudioConsent -> Text("This room's source has no confirmed audio in your languages. Start anyway to watch it with the audio it has.", style = MaterialTheme.typography.bodyMedium, color = t.text)
                            error != null -> Text("${error.title}. ${error.body}", style = MaterialTheme.typography.bodyMedium, color = t.danger)
                            else -> Text(watchTogetherHint(state.subject), style = MaterialTheme.typography.bodyMedium, color = t.text)
                        }
                        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            ShButton(
                                when { phase.starting -> "Starting…"; phase.needsAudioConsent -> "Start with this audio"; else -> "Start" },
                                onStart, Modifier.focusRequester(focus), icon = HeroIcons.Play, style = ButtonStyle.Primary,
                            )
                            ShButton("Cancel room", onCancel)
                        }
                    }
                }
            }
            if (phase is WatchTogether.Phase.Ready && phase.shareUrl != null) QrCode(phase.shareUrl.orEmpty(), size = 220.dp)
        }
        // Re-armed when the dialog moves on (opening → code → failure): the focused button may have gone.
        LaunchedEffect(phase::class) { withFrameNanos { }; runCatching { focus.requestFocus() } }
    }
}
