package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.repo.RecordOutcome
import com.streamhub.tv.core.data.repo.TEAM_MAX_LENGTH
import com.streamhub.tv.core.data.repo.isSportProgramme
import com.streamhub.tv.core.data.repo.recordingStateLabel
import com.streamhub.tv.core.data.repo.recordingStateMark
import com.streamhub.tv.core.data.repo.teamGuess
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/**
 * Select on a programme in the guide or on Live TV's now/next: Watch (while it is on air), Record
 * this, Record every new episode and, for a sports listing, Record every game for… with the team
 * prefilled from the title. Once something has been scheduled the dialog says so and offers Close.
 *
 * @param subtitle channel and times, already formatted by the caller
 * @param onWatch null when the programme is not on air
 * @param canRecordThis false when the programme has ended or is already scheduled
 */
@Composable
fun ProgrammeDialog(
    programme: Programme,
    subtitle: String,
    onWatch: (() -> Unit)?,
    canRecordThis: Boolean,
    busy: Boolean,
    outcome: RecordOutcome?,
    onRecord: () -> Unit,
    onRecordSeries: () -> Unit,
    onRecordTeam: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    val done = outcome as? RecordOutcome.Done
    val failed = outcome as? RecordOutcome.Failed
    var teamEntry by remember(programme.id) { mutableStateOf(false) }
    var team by remember(programme.id) { mutableStateOf(teamGuess(programme.title)) }
    val state = done?.state ?: programme.recordingState
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.width(520.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(programme.title, style = MaterialTheme.typography.titleLarge, color = t.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            recordingStateLabel(state)?.let { label ->
                Text(listOfNotNull(recordingStateMark(state), label).joinToString(" "), style = MaterialTheme.typography.labelLarge, color = t.accent)
            }
            when {
                done != null -> Text(done.message, style = MaterialTheme.typography.bodyLarge, color = t.text)
                failed != null -> Text("${failed.error.title}. ${failed.error.body}", style = MaterialTheme.typography.bodyMedium, color = t.danger)
                busy -> Text("Scheduling…", style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
                else -> programme.description?.takeIf(String::isNotBlank)?.let {
                    Text(it, style = MaterialTheme.typography.bodyMedium, color = t.textMuted, maxLines = 3, overflow = TextOverflow.Ellipsis)
                }
            }
            if (done != null) {
                // Scheduled: nothing more to choose, and pressing Select again must not schedule twice.
                if (onWatch != null) ShButton("Watch now", onWatch, Modifier.fillMaxWidth())
                ShButton("Close", onDismiss, Modifier.fillMaxWidth().focusRequester(focus), style = ButtonStyle.Primary)
            } else if (teamEntry) {
                ShTextField(
                    team, { team = it.take(TEAM_MAX_LENGTH) }, "Team to follow", Modifier.fillMaxWidth().focusRequester(focus),
                    placeholder = "Team name as the guide writes it", imeAction = ImeAction.Done, onImeAction = { if (!busy) onRecordTeam(team) },
                )
                Text("Every sports listing whose title names this team is recorded, on any channel.", style = MaterialTheme.typography.labelMedium, color = t.textMuted)
                ShButton("Record every game", { if (!busy) onRecordTeam(team) }, Modifier.fillMaxWidth(), style = ButtonStyle.Primary)
                ShButton("Back", { teamEntry = false }, Modifier.fillMaxWidth())
            } else {
                // Focus goes to the first action there is. The buttons stay enabled while one is running
                // (a press is ignored then), so focus is never stranded on a button that just went grey.
                val actions = buildList<Triple<String, ButtonStyle, () -> Unit>> {
                    if (onWatch != null) add(Triple("Watch", ButtonStyle.Primary, onWatch))
                    if (canRecordThis) add(Triple("Record this", ButtonStyle.Secondary) { if (!busy) onRecord() })
                    add(Triple("Record every new episode", ButtonStyle.Secondary) { if (!busy) onRecordSeries() })
                    if (isSportProgramme(programme)) add(Triple("Record every game for…", ButtonStyle.Secondary) { if (!busy) teamEntry = true })
                    add(Triple("Close", ButtonStyle.Secondary, onDismiss))
                }
                actions.forEachIndexed { i, (label, style, onClick) ->
                    ShButton(label, onClick, if (i == 0) Modifier.fillMaxWidth().focusRequester(focus) else Modifier.fillMaxWidth(), style = style)
                }
            }
        }
        // Re-armed when the dialog changes shape (team entry, scheduled): the focused button is gone.
        LaunchedEffect(programme.id, teamEntry, done != null) { withFrameNanos { }; runCatching { focus.requestFocus() } }
    }
}
