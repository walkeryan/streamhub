package com.streamhub.tv.feature.onboarding

import android.app.Activity
import android.os.SystemClock
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Icon
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.SwitchableUser
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.ErrorDialog
import com.streamhub.tv.core.ui.components.LoadingView
import com.streamhub.tv.core.ui.components.PinPadDialog
import com.streamhub.tv.core.ui.components.SafeArea
import com.streamhub.tv.core.ui.components.ScreenBackground
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.InitialFocusOnce
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.delay

/**
 * "Who's watching?": the people who can be picked on a shared TV, as a D-pad grid.
 *
 * [gate] is the cold-start picker: it stands between the launcher and the app, so Back does not
 * dismiss it (it offers "Exit app") and picking the person who is already signed in still asks for
 * their PIN. Opened from the rail it is a visit: Back returns, and the current person just closes it.
 *
 * @param onAbsent the server has no picker, or this is a personal device
 */
@Composable
fun WhoIsWatchingScreen(
    gate: Boolean,
    onDone: () -> Unit,
    onAbsent: () -> Unit,
    onBack: () -> Unit,
    vm: WhoIsWatchingViewModel = hiltViewModel(),
) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val activity = LocalContext.current as? Activity
    var askExit by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { vm.events.collect { if (it == WhoEvent.Done) onDone() else onAbsent() } }
    BackHandler { if (gate) askExit = true else onBack() }

    ScreenBackground {
        val error = s.loadError
        when {
            s.loading -> LoadingView()
            error != null -> {
                val retry = remember { FocusRequester() }
                LaunchedEffect(error) { withFrameNanos { }; runCatching { retry.requestFocus() } }
                Column(Modifier.fillMaxSize().padding(SafeArea), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                    Text(error.title, style = MaterialTheme.typography.headlineSmall, color = t.text)
                    Text(error.body, style = MaterialTheme.typography.bodyLarge, color = t.textMuted, modifier = Modifier.padding(top = 8.dp).width(560.dp))
                    Row(Modifier.padding(top = 24.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        ShButton("Try again", vm::load, Modifier.focusRequester(retry), style = ButtonStyle.Primary)
                        if (gate) ShButton("Exit app", { activity?.finish() }) else ShButton("Back", onBack)
                    }
                }
            }
            else -> People(s, gate, onPick = { vm.pick(it, keepCurrent = !gate) })
        }
    }

    s.pinFor?.let { user ->
        val locked by produceState(0, s.lockedUntilMs) {
            while (true) {
                val left = ((s.lockedUntilMs - SystemClock.elapsedRealtime() + 999) / 1000).toInt().coerceAtLeast(0)
                value = left
                if (left == 0) { vm.lockoutOver(); break }
                delay(250)
            }
        }
        PinPadDialog(
            title = "PIN for ${user.displayName.ifBlank { "this person" }}",
            hint = "Enter the PIN to continue as ${user.displayName.ifBlank { "this person" }}.",
            error = s.pinError,
            lockedSeconds = locked,
            busy = s.busy,
            onSubmit = vm::submitPin,
            onDismiss = vm::cancelPin,
            attempt = s.pinAttempt,
        )
    }
    s.notice?.let { ErrorDialog(it, onRetry = vm::dismissNotice, onDismiss = vm::dismissNotice) }
    if (askExit) ExitDialog(onStay = { askExit = false }, onExit = { activity?.finish() })
}

@Composable
private fun People(s: WhoState, gate: Boolean, onPick: (SwitchableUser) -> Unit) {
    val t = LocalThemeTokens.current
    val first = remember { FocusRequester() }
    val grid = remember { FocusRequester() }
    // Focus starts on whoever was watching last; on return from a dialog the grid restores its own.
    val startId = (s.users.firstOrNull { it.current } ?: s.users.first()).id
    InitialFocusOnce(initial = first, restore = grid)
    Column(Modifier.fillMaxSize().padding(SafeArea), horizontalAlignment = Alignment.CenterHorizontally) {
        Text("Who's watching?", style = MaterialTheme.typography.displaySmall, color = t.text, modifier = Modifier.padding(top = 24.dp))
        Text(
            if (gate) "Choose yourself to start. Press Back to leave StreamHub." else "Choose who is watching now. Press Back to return.",
            style = MaterialTheme.typography.bodyLarge, color = t.textMuted, modifier = Modifier.padding(top = 8.dp, bottom = 24.dp),
        )
        LazyVerticalGrid(
            columns = GridCells.Adaptive(CARD_W),
            // No wider than the people there are, so a short list sits in the middle of the screen.
            modifier = Modifier.width((CARD_W + CARD_GAP) * s.users.size.coerceIn(1, 5)).weight(1f).focusRequester(grid).focusRestorer(),
            contentPadding = PaddingValues(8.dp),
            horizontalArrangement = Arrangement.spacedBy(CARD_GAP),
            verticalArrangement = Arrangement.spacedBy(CARD_GAP),
        ) {
            items(s.users, key = { it.id }) { user ->
                PersonCard(user, s.serverUrl, busy = s.busy, onClick = { onPick(user) }, modifier = if (user.id == startId) Modifier.focusRequester(first) else Modifier)
            }
        }
    }
}

private val CARD_W = 168.dp
private val CARD_GAP = 16.dp

@Composable
private fun PersonCard(user: SwitchableUser, serverUrl: String?, busy: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    val name = user.displayName.ifBlank { "Unnamed" }
    FocusSurface(
        onClick = { if (!busy) onClick() },
        modifier = modifier.size(CARD_W, 212.dp),
        shape = RoundedCornerShape(Tokens.Radius.Lg),
        containerColor = t.surface,
        focusedContainerColor = t.surfaceAlt,
    ) {
        Column(Modifier.fillMaxSize().padding(12.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Box(Modifier.size(96.dp).background(t.accent, CircleShape), contentAlignment = Alignment.Center) {
                Text(personGlyph(user), style = MaterialTheme.typography.headlineMedium, color = t.accentText)
                // Drawn over the initial; a picture that fails to load simply leaves the initial showing.
                personImage(user, serverUrl)?.let { AsyncImage(it, contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize().clip(CircleShape)) }
            }
            Text(name, style = MaterialTheme.typography.titleMedium, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                listOfNotNull(user.roleName?.takeIf(String::isNotBlank), if (user.current) "Watching now" else null).joinToString(" · "),
                style = MaterialTheme.typography.labelMedium, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            if (user.hasPin) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                // Icon and word: the lock is not carried by colour or shape alone.
                Icon(Icons.Filled.Lock, contentDescription = null, tint = t.textMuted, modifier = Modifier.size(14.dp))
                Text("PIN", style = MaterialTheme.typography.labelMedium, color = t.textMuted)
            }
        }
    }
}

/** Back on the cold-start picker: there is nothing behind it to go back to, so the choice is to stay or leave the app. */
@Composable
private fun ExitDialog(onStay: () -> Unit, onExit: () -> Unit) {
    val t = LocalThemeTokens.current
    val stay = remember { FocusRequester() }
    Dialog(onDismissRequest = onStay) {
        Column(Modifier.width(460.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(28.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Leave StreamHub?", style = MaterialTheme.typography.titleLarge, color = t.text)
            Text("Someone has to be chosen before anything can be watched on this TV.", style = MaterialTheme.typography.bodyLarge, color = t.textMuted)
            Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                ShButton("Choose who's watching", onStay, Modifier.focusRequester(stay), style = ButtonStyle.Primary)
                ShButton("Exit app", onExit)
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { stay.requestFocus() } }
    }
}
