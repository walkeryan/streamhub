package com.streamhub.tv.feature.search

import android.view.KeyEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.nativeKeyCode
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.Room
import com.streamhub.tv.core.data.room.RoomCode
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.InitialFocusOnce
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

private const val KEYS_PER_ROW = 8

/**
 * Join a watch room by its six-character code. Left: the six slots and a picker of the code
 * alphabet for the D-pad (the remote's 2-9 keys and a keyboard's letters type directly). Right: the
 * room the code names, shown before it is joined, with Join; a wrong code says so there.
 */
@Composable
fun JoinRoomScreen(vm: JoinRoomViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val rail = LocalRailController.current
    val firstKey = remember { FocusRequester() }
    val screen = remember { FocusRequester() }
    val action = remember { FocusRequester() }
    InitialFocusOnce(initial = firstKey, restore = screen)
    var keysFocused by remember { mutableStateOf(false) }
    // The code is complete and answered: move to the button that acts on the answer. When that button
    // goes away again (the code was edited, a lookup is running) focus must not go with it.
    LaunchedEffect(s.room?.code, s.error, s.looking) {
        withFrameNanos { }
        if (s.room != null || s.error != null) runCatching { action.requestFocus() }
        else if (!keysFocused) runCatching { firstKey.requestFocus() }
    }

    Row(
        Modifier
            .fillMaxSize()
            .padding(vertical = Tokens.Canvas.SafeMarginY)
            .focusRequester(screen)
            .focusRestorer()
            .focusGroup()
            // Typed characters go to the code wherever focus is; D-pad and Select stay with the focused key.
            .onPreviewKeyEvent { e ->
                val code = e.key.nativeKeyCode
                val c = RoomCode.charOf(code)
                when {
                    c != null -> { if (e.type == KeyEventType.KeyDown) vm.type(c); true }
                    code == KeyEvent.KEYCODE_DEL -> { if (e.type == KeyEventType.KeyDown) vm.backspace(); true }
                    else -> false
                }
            },
    ) {
        Column(
            Modifier.width(520.dp).fillMaxHeight().padding(start = Tokens.Canvas.SafeMarginX).onFocusChanged { keysFocused = it.hasFocus },
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Join a room", style = MaterialTheme.typography.displaySmall, color = t.text)
            Text("Enter the six-character code on the host's screen.", style = MaterialTheme.typography.bodyLarge, color = t.textMuted)
            Row(
                Modifier.padding(vertical = 8.dp).semantics { contentDescription = "Code so far: ${s.entry.ifEmpty { "empty" }}" },
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                repeat(RoomCode.LENGTH) { i ->
                    Box(Modifier.size(56.dp, 68.dp).background(t.surfaceAlt, RoundedCornerShape(Tokens.Radius.Md)), contentAlignment = Alignment.Center) {
                        Text(
                            s.entry.getOrNull(i)?.toString() ?: if (i == s.entry.length) "_" else "",
                            style = MaterialTheme.typography.headlineMedium.copy(fontFamily = FontFamily.Monospace),
                            color = if (i < s.entry.length) t.text else t.textMuted,
                        )
                    }
                    // A gap after the third slot, as the code is shown to the host: "ABC 234".
                    if (i == 2) Box(Modifier.width(6.dp))
                }
            }
            RoomCode.ALPHABET.chunked(KEYS_PER_ROW).forEachIndexed { r, row ->
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    row.forEachIndexed { c, char ->
                        CodeKey(
                            char.toString(), { vm.type(char) },
                            Modifier
                                .then(if (r == 0 && c == 0) Modifier.focusRequester(firstKey) else Modifier)
                                .then(if (c == 0) Modifier.leftOpensRail(rail) else Modifier),
                        )
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                CodeKey("⌫ Delete", vm::backspace, Modifier.leftOpensRail(rail).semantics { contentDescription = "Delete last character" }, wide = true)
                CodeKey("Clear", vm::clear, wide = true)
            }
            Text("The remote's 2–9 keys type too. Codes never contain I, O, 0 or 1.", style = MaterialTheme.typography.labelMedium, color = t.textMuted)
        }
        Column(Modifier.weight(1f).fillMaxHeight().padding(start = 48.dp, end = Tokens.Canvas.SafeMarginX, top = 72.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            val room = s.room
            val error = s.error
            when {
                s.looking -> Text("Looking for the room…", style = MaterialTheme.typography.bodyLarge, color = t.textMuted)
                room != null -> RoomPreview(room, s, onJoin = vm::join, joinFocus = action)
                error != null -> {
                    Text(error.title, style = MaterialTheme.typography.titleLarge, color = t.text)
                    Text(error.body, style = MaterialTheme.typography.bodyLarge, color = t.textMuted)
                    // A wrong code is fixed on the left; anything else (server away, no permission) can be asked again.
                    if (error.canRetry) ShButton("Try again", vm::lookUp, Modifier.focusRequester(action), style = ButtonStyle.Primary)
                    else ShButton("Clear the code", vm::clear, Modifier.focusRequester(action))
                }
                else -> Text(
                    "The room appears here once all six characters are in.",
                    style = MaterialTheme.typography.bodyLarge, color = t.textMuted,
                )
            }
        }
    }
}

@Composable
private fun RoomPreview(room: Room, s: JoinRoomState, onJoin: () -> Unit, joinFocus: FocusRequester) {
    val t = LocalThemeTokens.current
    Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
        if (room.title.poster != null) {
            AsyncImage(room.title.poster, room.title.title, contentScale = ContentScale.Crop, modifier = Modifier.size(120.dp, 180.dp).clip(RoundedCornerShape(Tokens.Radius.Md)))
        }
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(room.title.title.ifBlank { "Watch room" }, style = MaterialTheme.typography.headlineSmall, color = t.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(
                listOfNotNull(room.title.subtitle?.takeIf(String::isNotBlank), room.title.year?.toString(), if (room.live) "Live" else null).joinToString("  ·  "),
                style = MaterialTheme.typography.bodyMedium, color = t.textMuted, maxLines = 1,
            )
            Text("Hosted by ${room.hostName.ifBlank { "someone" }}", style = MaterialTheme.typography.bodyLarge, color = t.text)
            Text(
                when (val n = room.members.size) { 0 -> "Nobody is in the room yet"; 1 -> "1 person watching"; else -> "$n people watching" } +
                    if (room.maxMembers > 0) " · room for ${room.maxMembers}" else "",
                style = MaterialTheme.typography.bodyMedium, color = t.textMuted,
            )
            Text(
                when {
                    room.live -> "Live channel"
                    room.state.playing -> "Playing now: you join where the room is"
                    else -> "Paused"
                },
                style = MaterialTheme.typography.bodyMedium, color = t.textMuted,
            )
        }
    }
    when {
        s.needsAudioConsent -> Text("This room's source has no confirmed audio in your languages. Join anyway to watch it with the audio it has.", style = MaterialTheme.typography.bodyMedium, color = t.text)
        s.error != null -> Text("${s.error.title}. ${s.error.body}", style = MaterialTheme.typography.bodyMedium, color = t.danger)
    }
    ShButton(
        when { s.joining -> "Joining…"; s.needsAudioConsent -> "Join with this audio"; else -> "Join" },
        onJoin, Modifier.focusRequester(joinFocus), style = ButtonStyle.Primary,
    )
}

@Composable
private fun CodeKey(label: String, onClick: () -> Unit, modifier: Modifier = Modifier, wide: Boolean = false) {
    val t = LocalThemeTokens.current
    FocusSurface(
        onClick = onClick,
        modifier = modifier.size(if (wide) 120.dp else 50.dp, 48.dp),
        shape = RoundedCornerShape(Tokens.Radius.Md),
        containerColor = t.surfaceAlt,
        focusedContainerColor = t.text,
        scaleOnFocus = false,
        glow = false,
    ) { focused ->
        Text(label, Modifier.align(Alignment.Center), style = MaterialTheme.typography.titleMedium, color = if (focused) t.bg else t.text, maxLines = 1)
    }
}
