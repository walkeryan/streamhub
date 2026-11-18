package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.repo.RoomSubject
import com.streamhub.tv.core.data.repo.WatchTogether
import com.streamhub.tv.core.data.room.RoomCode
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.glass

/** What happens once Start is pressed, by what the room is opened on. */
internal fun watchTogetherHint(subject: RoomSubject): String = when (subject) {
    RoomSubject.Title -> "Start when you're ready. It waits, paused, until someone presses Play."
    RoomSubject.Series -> "Start when you're ready. The room opens on the next episode to watch and waits, paused, until someone presses Play."
    RoomSubject.Channel -> "Start when you're ready. A live room has no position: everyone watches the channel as it airs, and Play and Pause apply to everyone."
}

/** The same, for a room started from the player: Start keeps what is playing going, in the room. */
internal fun watchTogetherCarryOnHint(subject: RoomSubject): String = when (subject) {
    RoomSubject.Channel -> "Start keeps this channel playing here, with you as the room's host. A live room has no position: everyone watches the channel as it airs."
    else -> "Start keeps this playing here from where you are, with you as the room's host. Everyone who joins catches up with you."
}

/**
 * The room's code, large enough to read across the room, and a QR code of `<server>/room/<code>` for
 * phones and computers. Start joins the room and opens the player; Back or Cancel closes the room.
 * Shared by a title's page, an episode's actions, Live TV and the player's menu ([carryOn]: the
 * player moves what it is playing into the room rather than opening it afresh).
 */
@Composable
fun WatchTogetherDialog(state: WatchTogether, onStart: () -> Unit, onRetry: () -> Unit, onCancel: () -> Unit, carryOn: Boolean = false) {
    Dialog(onDismissRequest = onCancel, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        WatchTogetherCard(state, onStart, onRetry, onCancel, carryOn)
    }
}

/** The room's card, in glass: what it is and the code on the left, the QR code on a white tile on the right. */
@Composable
internal fun WatchTogetherCard(state: WatchTogether, onStart: () -> Unit, onRetry: () -> Unit, onCancel: () -> Unit, carryOn: Boolean = false) {
    val t = LocalThemeTokens.current
    val g = glass
    val focus = remember { FocusRequester() }
    val phase = state.phase
    GlassPanel(Modifier.width(840.dp), padding = PaddingValues(36.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(36.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Image(GlassIcons.People, null, Modifier.size(28.dp), colorFilter = ColorFilter.tint(t.accent))
                    DialogTitle("Watch together", maxLines = 1)
                }
                DialogText(state.title, maxLines = 1, color = g.text)
                when (phase) {
                    WatchTogether.Phase.Creating -> {
                        DialogText("Opening a room…", color = g.text, modifier = Modifier.padding(vertical = 8.dp))
                        ShButton("Cancel", onCancel, Modifier.focusRequester(focus))
                    }
                    is WatchTogether.Phase.Failed -> {
                        Text(phase.error.title, color = t.danger, fontSize = 20.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 6.dp))
                        DialogText(phase.error.body)
                        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            if (phase.error.canRetry) ShButton("Try again", onRetry, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
                            ShButton("Close", onCancel, if (phase.error.canRetry) Modifier else Modifier.focusRequester(focus))
                        }
                    }
                    is WatchTogether.Phase.Ready -> {
                        GroupLabel("Room code", Modifier.padding(top = 8.dp))
                        BigCode(RoomCode.display(phase.room.code))
                        DialogText(
                            "On another TV: open Join a room from the menu and enter this code. On a phone or computer: scan the QR code" +
                                (phase.shareUrl?.let { " or open $it" } ?: "") + ".",
                            fontSize = 16.sp,
                        )
                        val error = phase.error
                        when {
                            phase.needsAudioConsent -> DialogText("This room's source has no confirmed audio in your languages. Start anyway to watch it with the audio it has.", color = g.text, fontSize = 16.sp)
                            error != null -> DialogText("${error.title}. ${error.body}", color = t.danger, fontSize = 16.sp)
                            else -> DialogText(if (carryOn) watchTogetherCarryOnHint(state.subject) else watchTogetherHint(state.subject), color = g.text, fontSize = 16.sp)
                        }
                        Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            ShButton(
                                when { phase.starting -> "Starting…"; phase.needsAudioConsent -> "Start with this audio"; else -> "Start" },
                                onStart, Modifier.focusRequester(focus), icon = HeroIcons.Play, style = ButtonStyle.Primary,
                            )
                            ShButton("Cancel room", onCancel)
                        }
                    }
                }
            }
            if (phase is WatchTogether.Phase.Ready && phase.shareUrl != null) QrTile(phase.shareUrl.orEmpty(), size = 232.dp)
        }
    }
    // Re-armed when the dialog moves on (opening → code → failure): the focused button may have gone.
    LaunchedEffect(phase::class) { withFrameNanos { }; runCatching { focus.requestFocus() } }
}
