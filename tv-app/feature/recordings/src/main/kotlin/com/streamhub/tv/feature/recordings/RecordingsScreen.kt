package com.streamhub.tv.feature.recordings

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.Recording
import com.streamhub.tv.core.data.model.RecordingRule
import com.streamhub.tv.core.data.model.RecordingStates
import com.streamhub.tv.core.data.repo.isUpcoming
import com.streamhub.tv.core.data.repo.recordingStateLabel
import com.streamhub.tv.core.data.repo.recordingStateMark
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.CenterMessage
import com.streamhub.tv.core.ui.components.FocusRow
import com.streamhub.tv.core.ui.components.LoadingView
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.SectionTitle
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.InitialFocusOnce
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val WHEN = DateTimeFormatter.ofPattern("EEE d MMM, HH:mm")
private val UNTIL = DateTimeFormatter.ofPattern("HH:mm")

/** "Fri 13 Nov, 20:00–21:00" in the TV's time zone; the raw text when it is not a timestamp. */
internal fun recordingWhen(startsAt: String, endsAt: String, zone: ZoneId = ZoneId.systemDefault()): String = runCatching {
    val start = Instant.parse(startsAt).atZone(zone)
    val end = runCatching { Instant.parse(endsAt).atZone(zone) }.getOrNull()
    WHEN.format(start) + (end?.let { "–" + UNTIL.format(it) } ?: "")
}.getOrDefault(startsAt)

internal fun ruleSummary(rule: RecordingRule): String = when (rule.kind) {
    "series" -> if (rule.newOnly) "Every new episode" else "Every episode"
    "team" -> "Every game"
    "keyword" -> "Titles containing"
    else -> rule.kind.replaceFirstChar(Char::uppercase)
}

/**
 * Recordings: three rows, Upcoming · Recorded · Rules. Select on a card opens what can be done with
 * it (cancel, delete, disable) and, for a recording that failed, why. Recorded items are not played
 * yet (see [RECORDED_PLAYBACK_ENABLED]).
 */
@Composable
fun RecordingsScreen(vm: RecordingsViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val error = s.error
    when {
        s.loading -> LoadingView()
        error != null && s.isEmpty -> CenterMessage(error.title, error.body) { ShButton("Try again", vm::load, style = ButtonStyle.Primary) }
        !s.enabled -> CenterMessage(
            "Recording isn't set up",
            "The StreamHub server needs a recordings folder before anything can be recorded. It is set in the server's settings on the web.",
        )
        s.isEmpty -> CenterMessage(
            "Nothing scheduled or recorded",
            "In the Guide or Live TV, press Select on a programme to record it once, every new episode, or every game for a team.",
        )
        else -> RecordingRows(s, vm)
    }
    when (val selected = s.selected) {
        is RecordingsSelection.Item -> RecordingDialog(selected.recording, s, onRemove = vm::removeSelected, onDismiss = vm::dismiss)
        is RecordingsSelection.Rule -> RuleDialog(selected.rule, s, onToggle = vm::toggleSelectedRule, onDelete = vm::deleteSelectedRule, onDismiss = vm::dismiss)
        null -> Unit
    }
}

@Composable
private fun RecordingRows(s: RecordingsState, vm: RecordingsViewModel) {
    val t = LocalThemeTokens.current
    val first = remember { FocusRequester() }
    val list = remember { FocusRequester() }
    InitialFocusOnce(initial = first, restore = list)
    // A card that was cancelled or deleted took focus with it: start again from the first card.
    LaunchedEffect(s.changes) { if (s.changes > 0) { withFrameNanos { }; runCatching { first.requestFocus() } } }
    // The first card of the first row that has any holds the initial focus.
    val firstRow = when { s.upcoming.isNotEmpty() -> 0; s.recorded.isNotEmpty() -> 1; else -> 2 }
    fun firstOf(row: Int, index: Int) = if (row == firstRow && index == 0) Modifier.focusRequester(first) else Modifier
    PivotScroll(0.dp) {
        LazyColumn(Modifier.fillMaxSize().focusRequester(list).focusRestorer(), contentPadding = PaddingValues(vertical = Tokens.Canvas.SafeMarginY)) {
            item("head") {
                Column(Modifier.padding(start = Tokens.Canvas.SafeMarginX, bottom = 16.dp)) {
                    Text("Recordings", style = MaterialTheme.typography.displaySmall, color = t.text)
                    if (!s.canSchedule) Text("You can see recordings here; scheduling and removing them needs permission from the owner.", style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
                }
            }
            item("upcoming") {
                if (s.upcoming.isEmpty()) EmptyRow("Upcoming", "Nothing is scheduled.")
                else FocusRow(s.upcoming, key = { it.id }, title = "Upcoming") { i, r, m -> RecordingCard(r, { vm.open(r) }, m.then(firstOf(0, i))) }
            }
            item("recorded") {
                if (s.recorded.isEmpty()) EmptyRow("Recorded", "Nothing has been recorded yet.")
                else FocusRow(s.recorded, key = { it.id }, title = "Recorded") { i, r, m -> RecordingCard(r, { vm.open(r) }, m.then(firstOf(1, i))) }
            }
            item("rules") {
                if (s.rules.isEmpty()) EmptyRow("Rules", "No rules. \"Record every new episode\" and \"Record every game for…\" make them.")
                else FocusRow(s.rules, key = { it.id }, title = "Rules") { i, rule, m -> RuleCard(rule, { vm.open(rule) }, m.then(firstOf(2, i))) }
            }
        }
    }
}

@Composable
private fun EmptyRow(title: String, text: String) {
    val t = LocalThemeTokens.current
    Column(Modifier.padding(start = Tokens.Canvas.SafeMarginX, bottom = 20.dp)) {
        SectionTitle(title)
        Text(text, style = MaterialTheme.typography.bodyMedium, color = t.textMuted, modifier = Modifier.padding(top = 8.dp))
    }
}

private val CARD_W = 300.dp
private val CARD_H = 132.dp

@Composable
private fun RecordingCard(r: Recording, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    val failed = r.state == RecordingStates.FAILED || r.state == RecordingStates.MISSED
    FocusSurface(onClick = onClick, modifier = modifier.size(CARD_W, CARD_H), containerColor = t.surface, focusedContainerColor = t.surfaceAlt) {
        Column(Modifier.fillMaxSize().padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(r.title.ifBlank { "Untitled" }, style = MaterialTheme.typography.titleSmall, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                listOfNotNull(r.channelName?.takeIf(String::isNotBlank), episodeLabel(r), r.subtitle?.takeIf(String::isNotBlank)).joinToString(" · "),
                style = MaterialTheme.typography.labelMedium, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            Text(recordingWhen(r.startsAt, r.endsAt), style = MaterialTheme.typography.labelMedium, color = t.textMuted, maxLines = 1)
            // State as a mark and words; colour only underlines it.
            Text(
                listOfNotNull(recordingStateMark(r.state), recordingStateLabel(r.state)).joinToString(" "),
                style = MaterialTheme.typography.labelLarge, color = if (failed) t.danger else t.accent, maxLines = 1,
            )
            if (failed && !r.error.isNullOrBlank()) Text(r.error.orEmpty(), style = MaterialTheme.typography.labelSmall, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

private fun episodeLabel(r: Recording): String? = when {
    r.season != null && r.episode != null -> "S${r.season} E${r.episode}"
    r.episode != null -> "Episode ${r.episode}"
    else -> null
}

@Composable
private fun RuleCard(rule: RecordingRule, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    FocusSurface(onClick = onClick, modifier = modifier.size(CARD_W, CARD_H), containerColor = t.surface, focusedContainerColor = t.surfaceAlt) {
        Column(Modifier.fillMaxSize().padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(ruleSummary(rule), style = MaterialTheme.typography.labelMedium, color = t.textMuted, maxLines = 1)
            Text(rule.match.ifBlank { "Unnamed" }, style = MaterialTheme.typography.titleSmall, color = t.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(rule.channelName?.takeIf(String::isNotBlank) ?: "Any channel", style = MaterialTheme.typography.labelMedium, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(if (rule.enabled) "✓ On" else "Off", style = MaterialTheme.typography.labelLarge, color = if (rule.enabled) t.accent else t.textMuted)
        }
    }
}

@Composable
private fun DetailDialog(title: String, onDismiss: () -> Unit, focus: FocusRequester, content: @Composable () -> Unit) {
    val t = LocalThemeTokens.current
    Dialog(onDismissRequest = onDismiss) {
        Column(Modifier.width(520.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(28.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge, color = t.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            content()
        }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { focus.requestFocus() } }
    }
}

@Composable
private fun RecordingDialog(r: Recording, s: RecordingsState, onRemove: () -> Unit, onDismiss: () -> Unit) {
    val t = LocalThemeTokens.current
    val launcher = LocalPlayLauncher.current
    val focus = remember { FocusRequester() }
    val upcoming = r.isUpcoming()
    val playable = RECORDED_PLAYBACK_ENABLED && r.state == RecordingStates.DONE
    DetailDialog(r.title.ifBlank { "Recording" }, onDismiss, focus) {
        Text(listOfNotNull(r.channelName?.takeIf(String::isNotBlank), recordingWhen(r.startsAt, r.endsAt)).joinToString("  ·  "), style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
        Text(listOfNotNull(recordingStateMark(r.state), recordingStateLabel(r.state)).joinToString(" "), style = MaterialTheme.typography.bodyLarge, color = t.text)
        if (!r.error.isNullOrBlank()) Text("Reason: ${r.error}", style = MaterialTheme.typography.bodyMedium, color = t.danger)
        when {
            upcoming -> Text(
                if (r.ruleId != null) "Scheduled by a rule. Cancelling skips just this one; the rule keeps recording the others." else "Cancelling removes it from the schedule.",
                style = MaterialTheme.typography.bodyMedium, color = t.textMuted,
            )
            r.state == RecordingStates.DONE && !playable -> Text("Recordings can't be played on this TV yet. That is on its way.", style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
        }
        s.actionError?.let { Text("${it.title}. ${it.body}", style = MaterialTheme.typography.bodyMedium, color = t.danger) }
        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            if (playable) ShButton("Play", { onDismiss(); launcher.play("rec:${r.id}") }, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
            if (s.canSchedule) {
                ShButton(
                    when { s.busy -> "Working…"; upcoming -> "Cancel recording"; else -> "Delete" },
                    onRemove, if (playable) Modifier else Modifier.focusRequester(focus), style = ButtonStyle.Danger,
                )
            }
            ShButton("Close", onDismiss, if (playable || s.canSchedule) Modifier else Modifier.focusRequester(focus))
        }
    }
}

@Composable
private fun RuleDialog(rule: RecordingRule, s: RecordingsState, onToggle: () -> Unit, onDelete: () -> Unit, onDismiss: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    DetailDialog("${ruleSummary(rule)}: ${rule.match}", onDismiss, focus) {
        Text(
            listOfNotNull(rule.channelName?.takeIf(String::isNotBlank) ?: "Any channel", if (rule.enabled) "On" else "Off", rule.keep?.let { "keeps the last $it" }).joinToString("  ·  "),
            style = MaterialTheme.typography.bodyMedium, color = t.textMuted,
        )
        Text(
            if (rule.enabled) "Turning it off drops the upcoming recordings it scheduled. Deleting it does the same; finished recordings stay."
            else "Turning it on schedules every matching programme in the guide again.",
            style = MaterialTheme.typography.bodyMedium, color = t.textMuted,
        )
        s.actionError?.let { Text("${it.title}. ${it.body}", style = MaterialTheme.typography.bodyMedium, color = t.danger) }
        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            if (s.canSchedule) {
                ShButton(when { s.busy -> "Working…"; rule.enabled -> "Turn off"; else -> "Turn on" }, onToggle, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
                ShButton("Delete rule", onDelete, style = ButtonStyle.Danger)
            }
            ShButton("Close", onDismiss, if (s.canSchedule) Modifier else Modifier.focusRequester(focus))
        }
    }
}
