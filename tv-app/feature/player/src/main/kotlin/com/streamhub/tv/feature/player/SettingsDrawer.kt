package com.streamhub.tv.feature.player

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.room.RoomCode
import com.streamhub.tv.core.player.TrackOption
import com.streamhub.tv.core.ui.components.GlassIcons
import com.streamhub.tv.core.ui.components.GlassPanel
import com.streamhub.tv.core.ui.components.GlassRow
import com.streamhub.tv.core.ui.components.GroupLabel
import com.streamhub.tv.core.ui.components.OnOffPill
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import com.streamhub.tv.core.ui.theme.glass

/** Everything the player's settings drawer shows (see [PlayerMenu], which fills it from the screen's state). */
internal data class SettingsModel(
    /** The watch room this playback is in; null outside a room. */
    val roomCode: String? = null,
    val roomMembers: Int = 0,
    val roomLocked: Boolean = false,
    val roomHost: Boolean = false,
    /** A live channel outside a room: buffered rewind / archive and Go Live. */
    val channel: Boolean = false,
    val hasBufferWindow: Boolean = false,
    val bufferedSeconds: Int? = null,
    val rewindError: String? = null,
    val bufferError: String? = null,
    val audioNotice: String? = null,
    val audioBlocked: Boolean = false,
    val trackMessage: String? = null,
    val audioTracks: List<TrackOption> = emptyList(),
    val textTracks: List<TrackOption> = emptyList(),
    val subtitlesOff: Boolean = true,
    /** The profile's "Autoplay next episode"; null hides it (nothing comes next). */
    val autoplay: Boolean? = null,
    val aspectLabel: String = "",
    val sleepOptions: List<Int?> = emptyList(),
    val sleepMinutes: Int? = null,
    val watchTogether: Boolean = false,
    val canRetry: Boolean = false,
)

internal class SettingsActions(
    val onToggleRoomLock: () -> Unit = {},
    val onInvite: () -> Unit = {},
    val onLeaveRoom: () -> Unit = {},
    val onRewind: () -> Unit = {},
    val onGoLive: () -> Unit = {},
    /** An audio track was chosen; [opener] is its row, for focus to come back to after a confirmation. */
    val onAudio: (TrackOption, FocusRequester) -> Unit = { _, _ -> },
    val onSubtitlesOff: () -> Unit = {},
    val onSubtitle: (TrackOption) -> Unit = {},
    val onToggleAutoplay: () -> Unit = {},
    val onAspect: () -> Unit = {},
    val onSleep: (Int?) -> Unit = {},
    val onWatchTogether: () -> Unit = {},
    val onStreamInfo: () -> Unit = {},
    val onRetry: () -> Unit = {},
    val onClose: () -> Unit = {},
)

/**
 * The player's settings drawer in the glass look: a sheet floating at the right edge with the
 * mockup's menu rows (icon, label, value; the accent ring on focus; a tick on the choice in force)
 * under small section captions. [first] takes the drawer's first focus: the room's first action in a
 * room, the archive on a channel, else the first audio track this TV can play (else Subtitles off).
 * [streamInfoOpener] and [inviteOpener] are where focus returns when their dialogs close.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun SettingsDrawer(
    m: SettingsModel,
    a: SettingsActions,
    first: FocusRequester,
    streamInfoOpener: FocusRequester,
    inviteOpener: FocusRequester,
    modifier: Modifier = Modifier,
) {
    val t = LocalThemeTokens.current
    val g = glass
    val inRoom = m.roomCode != null
    /** A section above Audio holds the menu's first focus. */
    val topSection = m.channel || inRoom
    // Initial focus must land on something focusable: tracks this TV cannot decode are listed but disabled.
    val firstAudio = m.audioTracks.indexOfFirst { it.supported }
    val row = Modifier.fillMaxWidth()
    GlassPanel(
        modifier.fillMaxHeight().width(440.dp).padding(top = 14.dp, bottom = 14.dp, end = 14.dp),
        shape = RoundedCornerShape(20.dp),
        padding = PaddingValues(0.dp),
        verticalArrangement = Arrangement.Top,
    ) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = Tokens.Canvas.SafeMarginY - 6.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text("Settings", color = g.text, fontSize = 24.sp, fontWeight = FontWeight.ExtraBold, modifier = Modifier.padding(start = 10.dp, bottom = 6.dp))
            if (inRoom) {
                Section("Watch room")
                Column(Modifier.padding(start = 14.dp, bottom = 6.dp)) {
                    Text(RoomCode.display(m.roomCode.orEmpty()), color = t.accent, fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = 2.sp)
                    Text(
                        (if (m.roomMembers == 1) "1 person watching" else "${m.roomMembers} people watching") +
                            if (m.roomLocked) " · only the host controls playback" else "",
                        color = g.muted, fontSize = 15.sp, lineHeight = 21.sp, maxLines = 2,
                    )
                }
                if (m.roomHost) {
                    GlassRow(
                        if (m.roomLocked) "Let everyone control playback" else "Only I control playback", a.onToggleRoomLock,
                        row.focusRequester(first), icon = if (m.roomLocked) GlassIcons.Unlock else GlassIcons.Lock,
                    )
                    GlassRow("Invite people", a.onInvite, row.focusRequester(inviteOpener), icon = GlassIcons.People)
                    GlassRow("Leave the room", a.onLeaveRoom, row, icon = GlassIcons.Leave)
                } else {
                    GlassRow("Leave the room", a.onLeaveRoom, row.focusRequester(first), icon = GlassIcons.Leave)
                    GlassRow("Invite people", a.onInvite, row.focusRequester(inviteOpener), icon = GlassIcons.People)
                }
            }
            if (m.channel) {
                Section("Live")
                GlassRow(if (m.hasBufferWindow) "Buffered rewind" else "Provider archive · unverified", a.onRewind, row.focusRequester(first), icon = GlassIcons.History)
                GlassRow("Go Live", a.onGoLive, row, icon = GlassIcons.Live)
                m.rewindError?.let { Note(it, t.danger) }
                m.bufferedSeconds?.let { Note("$it seconds retained · grows while watched") }
                m.bufferError?.let { Note(it, t.danger) }
            }
            Section("Audio")
            m.audioNotice?.let { Note(it, if (m.audioBlocked) t.danger else g.muted) }
            m.trackMessage?.let { Note(it) }
            if (m.audioTracks.isEmpty()) Note("Audio track metadata unavailable")
            m.audioTracks.forEachIndexed { i, tr ->
                val opener = remember(tr.revision, tr.groupIndex, tr.trackIndex) { FocusRequester() }
                val note = when {
                    !tr.supported -> UNSUPPORTED_TRACK
                    tr.language.isNullOrBlank() || tr.language.equals("und", true) -> " · language unverified"
                    else -> ""
                }
                GlassRow(
                    tr.label + note, { a.onAudio(tr, opener) },
                    row.focusRequester(opener).then(if (i == firstAudio && !topSection) Modifier.focusRequester(first) else Modifier),
                    checkable = true, checked = tr.selected, enabled = tr.supported,
                )
            }
            Section("Subtitles")
            GlassRow("Off", a.onSubtitlesOff, row.then(if (firstAudio < 0 && !topSection) Modifier.focusRequester(first) else Modifier), checkable = true, checked = m.subtitlesOff)
            m.textTracks.forEach { tr ->
                GlassRow(tr.label + if (tr.supported) "" else UNSUPPORTED_TRACK, { a.onSubtitle(tr) }, row, checkable = true, checked = !m.subtitlesOff && tr.selected, enabled = tr.supported)
            }
            // The profile's "Autoplay next episode" (the web settings have the same switch).
            m.autoplay?.let { on ->
                Section("Playback")
                GlassRow("Autoplay next episode", a.onToggleAutoplay, row, icon = GlassIcons.Next, trailing = { OnOffPill(on) })
            }
            Section("Picture")
            GlassRow("Aspect", a.onAspect, row, icon = GlassIcons.Picture, detail = m.aspectLabel)
            Section("Sleep timer")
            FlowRow(
                Modifier.padding(start = 6.dp, top = 4.dp, bottom = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                m.sleepOptions.forEach { minutes -> ShChip(minutes?.let { "$it min" } ?: "Off", m.sleepMinutes == minutes, { a.onSleep(minutes) }) }
            }
            Divider()
            // A room for whatever is playing, with this TV as its host (the web player's "Watch together").
            if (m.watchTogether) GlassRow("Watch together", a.onWatchTogether, row, icon = GlassIcons.People)
            // What is playing and how it gets here: the server's facts and the player's own numbers.
            GlassRow("Stream info", a.onStreamInfo, row.focusRequester(streamInfoOpener), icon = GlassIcons.Info)
            if (m.canRetry) GlassRow("Retry stream", a.onRetry, row, icon = GlassIcons.Retry)
            GlassRow("Close controls", a.onClose, row, icon = GlassIcons.Close)
        }
    }
}

@Composable
private fun Section(label: String) {
    GroupLabel(label, Modifier.padding(start = 14.dp, top = 16.dp, bottom = 6.dp))
}

@Composable
private fun Note(text: String, color: Color = glass.muted) {
    Text(text, color = color, fontSize = 15.sp, lineHeight = 21.sp, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(start = 14.dp, end = 8.dp, bottom = 4.dp))
}

@Composable
private fun Divider() {
    Box(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 12.dp).height(1.dp).background(glass.line))
}
