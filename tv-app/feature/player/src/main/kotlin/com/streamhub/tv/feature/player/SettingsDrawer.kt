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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.room.RoomCode
import com.streamhub.tv.core.player.TrackOption
import com.streamhub.tv.core.ui.components.DialogText
import com.streamhub.tv.core.ui.components.DialogTitle
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.GlassIcons
import com.streamhub.tv.core.ui.components.GlassPanel
import com.streamhub.tv.core.ui.components.GlassRow
import com.streamhub.tv.core.ui.components.GroupLabel
import com.streamhub.tv.core.ui.components.OnOffPill
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.launch
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
    /** The profile's "Back shrinks to mini player"; null hides it (this player cannot minimise). */
    val backMinimises: Boolean? = null,
    val aspectLabel: String = "",
    val sleepOptions: List<Int?> = emptyList(),
    val sleepMinutes: Int? = null,
    val watchTogether: Boolean = false,
    val canRetry: Boolean = false,
    val recording: Boolean = false,
    val canSaveScene: Boolean = false,
)

internal class SettingsActions(
    val onToggleRoomLock: () -> Unit = {},
    val onInvite: () -> Unit = {},
    val onLeaveRoom: () -> Unit = {},
    val onRoomControls: () -> Unit = {},
    val onRewind: () -> Unit = {},
    val onGoLive: () -> Unit = {},
    /** An audio track was chosen; [opener] is its row, for focus to come back to after a confirmation. */
    val onAudio: (TrackOption, FocusRequester) -> Unit = { _, _ -> },
    val onSubtitlesOff: () -> Unit = {},
    val onSubtitle: (TrackOption) -> Unit = {},
    val onToggleAutoplay: () -> Unit = {},
    val onToggleBackMinimises: () -> Unit = {},
    val onAspect: () -> Unit = {},
    val onSleep: (Int?) -> Unit = {},
    val onWatchTogether: () -> Unit = {},
    val onStreamInfo: () -> Unit = {},
    val onReport: () -> Unit = {},
    val onSaveScene: () -> Unit = {},
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
    var shortcutsOpen by remember { mutableStateOf(false) }
    val shortcutsOpener = remember { FocusRequester() }
    val scope = rememberCoroutineScope()
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
                GlassRow("Room queue and host", a.onRoomControls, row, icon = GlassIcons.People)
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
            // The profile's "Autoplay next episode" and "Back shrinks to mini player" (the web settings have the same switches).
            if (m.autoplay != null || m.backMinimises != null) Section("Playback")
            m.autoplay?.let { on ->
                GlassRow("Autoplay next episode", a.onToggleAutoplay, row, icon = GlassIcons.Next, trailing = { OnOffPill(on) })
            }
            m.backMinimises?.let { on ->
                GlassRow("Back shrinks to mini player", a.onToggleBackMinimises, row, icon = GlassIcons.Minimise, trailing = { OnOffPill(on) })
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
            if (m.canSaveScene) GlassRow("Save a scene", a.onSaveScene, row, icon = GlassIcons.Info)
            if (m.recording) GlassRow("Report a recording issue", a.onReport, row, icon = GlassIcons.Info)
            GlassRow("Stream info", a.onStreamInfo, row.focusRequester(streamInfoOpener), icon = GlassIcons.Info)
            GlassRow("Controls and shortcuts", { shortcutsOpen = true }, row.focusRequester(shortcutsOpener), icon = GlassIcons.Info)
            if (m.canRetry) GlassRow("Retry stream", a.onRetry, row, icon = GlassIcons.Retry)
            GlassRow("Close controls", a.onClose, row, icon = GlassIcons.Close)
        }
    }
    if (shortcutsOpen) {
        RemoteShortcutDialog(m.channel, m.roomCode != null) {
            shortcutsOpen = false
            scope.launch { withFrameNanos { }; runCatching { shortcutsOpener.requestFocus() } }
        }
    }
}

@Composable
internal fun RemoteShortcutDialog(channel: Boolean, inRoom: Boolean, onClose: () -> Unit) {
    val closeFocus = remember { FocusRequester() }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        GlassPanel(Modifier.width(540.dp)) {
            DialogTitle("Controls and shortcuts")
            when {
                inRoom -> DialogText("Use the playback controls to play or pause together when the room allows it.")
                channel -> DialogText("Up or Down changes channel. OK opens the channel list. Left opens the archive when available.")
                else -> DialogText("Left or Right skips 10 seconds. OK or Down opens the timeline and playback controls.")
            }
            DialogText("Hold OK or press Menu to open settings. Use the arrows to highlight a control and OK to choose it.")
            DialogText("Back closes the current panel. On a keyboard, press ? during playback to open this guide.")
            ShButton("Close", onClose, Modifier.padding(top = 10.dp).focusRequester(closeFocus))
        }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { closeFocus.requestFocus() } }
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

@Composable
internal fun RoomQueueDialog(vm: PlayerViewModel, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var controls by remember { mutableStateOf<com.streamhub.tv.core.data.model.RoomControls?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var query by remember { mutableStateOf("") }
    var titles by remember { mutableStateOf<List<com.streamhub.tv.core.data.model.Tile>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }
    fun change(action: String, id: String = "") {
        if (busy) return
        busy = true
        scope.launch {
            try { controls = vm.roomControls(action, id); error = null }
            catch (e: kotlinx.coroutines.CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Could not update the room." }
            finally { busy = false }
        }
    }
    LaunchedEffect(Unit) {
        while (true) {
            try { if (!busy) controls = vm.roomControls() }
            catch (e: kotlinx.coroutines.CancellationException) { throw e }
            catch (e: Exception) { error = "Could not reach the room." }
            kotlinx.coroutines.delay(3500)
        }
    }
    LaunchedEffect(query) {
        titles = emptyList()
        if (query.trim().length >= 2) {
            kotlinx.coroutines.delay(300)
            try { titles = vm.roomSuggestions(query.trim()) }
            catch (e: kotlinx.coroutines.CancellationException) { throw e }
            catch (e: Exception) { error = "Search is unavailable." }
        }
    }
    Dialog(onDismissRequest = onClose) {
        GlassPanel(Modifier.fillMaxWidth().verticalScroll(rememberScrollState())) {
            DialogTitle("Room queue and host")
            DialogText("Find a title to suggest")
            androidx.compose.foundation.text.BasicTextField(value = query, onValueChange = { query = it },
                modifier = Modifier.fillMaxWidth().background(androidx.compose.ui.graphics.Color.DarkGray).padding(12.dp),
                textStyle = androidx.compose.ui.text.TextStyle(color = androidx.compose.ui.graphics.Color.White), singleLine = true)
            titles.forEach { ShButton("Suggest " + it.title, { change("suggest", it.id) }) }
            error?.let { DialogText(it) }
            controls?.let { state ->
                if (state.queue.isEmpty()) DialogText("The queue is empty.")
                state.queue.forEach { item ->
                    DialogText(item.title)
                    if (state.isHost) {
                        ShButton("Play for everyone", { change("advance", item.id) })
                        ShButton("Remove", { change("remove", item.id) })
                    }
                }
                if (state.isHost) state.members.filterNot { it.host }.forEach { other ->
                    ShButton(if (state.offeredTo == other.id) "Waiting for " + other.name else "Offer host to " + other.name,
                        { change("offer", other.id) })
                }
                if (state.offeredTo == state.memberId) {
                    DialogText("Take over as host?")
                    ShButton("Accept", { change("accept") })
                    ShButton("Decline", { change("decline") })
                }
            }
            ShButton("Done", onClose)
        }
    }
}
