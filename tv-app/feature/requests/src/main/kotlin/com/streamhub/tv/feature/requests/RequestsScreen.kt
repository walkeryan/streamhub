package com.streamhub.tv.feature.requests

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.MediaRequest
import com.streamhub.tv.core.data.model.RequestStates
import com.streamhub.tv.core.data.repo.requestStateLabel
import com.streamhub.tv.core.data.repo.requestStateText
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.CenterMessage
import com.streamhub.tv.core.ui.components.LoadingView
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.InitialFocusOnce
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** "All seasons", "Season 2", "Seasons 1, 3"; null for a film. */
internal fun seasonsLabel(request: MediaRequest): String? = when {
    request.mediaType != "tv" -> null
    request.seasons.isNullOrEmpty() -> "All seasons"
    request.seasons!!.size == 1 -> "Season ${request.seasons!!.first()}"
    else -> "Seasons ${request.seasons!!.sorted().joinToString(", ")}"
}

/**
 * Requests: "My requests", and for someone who may approve, "All requests" with Approve and Decline.
 * Select on a request opens it: where it stands, the note that came with a decision, and its actions.
 */
@Composable
fun RequestsScreen(vm: RequestsViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val rail = LocalRailController.current
    val launcher = LocalPlayLauncher.current
    val first = remember { FocusRequester() }
    val screen = remember { FocusRequester() }
    val error = s.error
    when {
        s.loading -> { LoadingView(); return }
        error != null && s.mine.isEmpty() && s.all.isEmpty() -> {
            val retry = remember { FocusRequester() }
            LaunchedEffect(Unit) { withFrameNanos { }; runCatching { retry.requestFocus() } }
            CenterMessage(error.title, error.body) { ShButton("Try again", vm::load, Modifier.focusRequester(retry), style = ButtonStyle.Primary) }
            return
        }
    }
    InitialFocusOnce(initial = first, restore = screen)
    Column(Modifier.fillMaxSize().padding(vertical = Tokens.Canvas.SafeMarginY).focusRequester(screen).focusRestorer()) {
        Text("Requests", style = MaterialTheme.typography.displaySmall, color = t.text, modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX))
        // Always something to hold focus, even when a list is empty.
        Row(Modifier.padding(start = Tokens.Canvas.SafeMarginX, top = 12.dp, bottom = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShChip("My requests", s.tab == RequestsTab.Mine, { vm.show(RequestsTab.Mine) }, Modifier.focusRequester(first).leftOpensRail(rail))
            if (s.canApprove) ShChip(if (s.waiting > 0) "All requests · ${s.waiting} waiting" else "All requests", s.tab == RequestsTab.All, { vm.show(RequestsTab.All) })
        }
        val shown = s.shown
        if (shown.isEmpty()) {
            Text(
                if (s.tab == RequestsTab.All) "Nobody has asked for anything yet."
                else "You haven't asked for anything yet. On a title that isn't available here, press Request.",
                style = MaterialTheme.typography.bodyLarge, color = t.textMuted, modifier = Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX),
            )
        } else PivotScroll(96.dp) {
            LazyColumn(
                Modifier.fillMaxSize().focusRestorer(),
                contentPadding = PaddingValues(start = Tokens.Canvas.SafeMarginX, end = Tokens.Canvas.SafeMarginX, bottom = 48.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                itemsIndexed(shown, key = { _, r -> r.id }) { _, r ->
                    RequestRow(r, showWho = s.tab == RequestsTab.All, onClick = { vm.open(r) }, modifier = Modifier.leftOpensRail(rail))
                }
            }
        }
    }
    s.selected?.let { request ->
        RequestDialog(
            request, s,
            onOpenTitle = { vm.dismiss(); if (request.titleId.isNotBlank()) launcher.openDetails(request.titleId) },
            onApprove = vm::approveSelected, onDecline = vm::declineSelected, onDismiss = vm::dismiss,
        )
    }
}

@Composable
private fun RequestRow(r: MediaRequest, showWho: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    FocusSurface(onClick = onClick, modifier = modifier.fillMaxWidth().height(84.dp), containerColor = t.surface, focusedContainerColor = t.surfaceAlt, scaleOnFocus = false, glow = false) {
        Row(Modifier.fillMaxSize().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Box(Modifier.size(44.dp, 66.dp).clip(RoundedCornerShape(Tokens.Radius.Sm)).background(t.surfaceAlt)) {
                if (r.poster != null) AsyncImage(r.poster, contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize())
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(r.title.ifBlank { "Untitled" } + (r.year?.let { " ($it)" } ?: ""), style = MaterialTheme.typography.titleSmall, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    listOfNotNull(r.profileName?.takeIf { showWho && it.isNotBlank() }?.let { "Asked by $it" }, seasonsLabel(r), r.note?.takeIf(String::isNotBlank)).joinToString("  ·  "),
                    style = MaterialTheme.typography.labelMedium, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            Text(
                requestStateLabel(r.state).orEmpty(), style = MaterialTheme.typography.labelLarge, maxLines = 1,
                color = if (r.state == RequestStates.DECLINED || r.state == RequestStates.FAILED) t.danger else t.accent,
            )
        }
    }
}

@Composable
private fun RequestDialog(r: MediaRequest, s: RequestsState, onOpenTitle: () -> Unit, onApprove: () -> Unit, onDecline: () -> Unit, onDismiss: () -> Unit) {
    val t = LocalThemeTokens.current
    val focus = remember { FocusRequester() }
    val decidable = canDecide(s, r)
    Dialog(onDismissRequest = onDismiss) {
        Column(Modifier.width(540.dp).background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(28.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(r.title.ifBlank { "Request" } + (r.year?.let { " ($it)" } ?: ""), style = MaterialTheme.typography.titleLarge, color = t.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(
                listOfNotNull(r.profileName?.takeIf(String::isNotBlank)?.let { "Asked by $it" }, seasonsLabel(r)).joinToString("  ·  "),
                style = MaterialTheme.typography.bodyMedium, color = t.textMuted,
            )
            // For the approver the sentence is about the decision to make, not about "your request".
            Text(
                if (decidable) "Waiting for a decision. Approving sends it on to be fetched." else requestStateText(r.state).orEmpty(),
                style = MaterialTheme.typography.bodyLarge, color = t.text,
            )
            r.note?.takeIf(String::isNotBlank)?.let { Text("Note: $it", style = MaterialTheme.typography.bodyMedium, color = t.textMuted) }
            s.actionError?.let { Text("${it.title}. ${it.body}", style = MaterialTheme.typography.bodyMedium, color = t.danger) }
            Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (decidable) {
                    ShButton(if (s.busy) "Working…" else "Approve", onApprove, Modifier.focusRequester(focus), style = ButtonStyle.Primary)
                    ShButton("Decline", onDecline, style = ButtonStyle.Danger)
                }
                ShButton("Open title", onOpenTitle, if (decidable) Modifier else Modifier.focusRequester(focus))
                ShButton("Close", onDismiss)
            }
        }
        LaunchedEffect(Unit) { withFrameNanos { }; runCatching { focus.requestFocus() } }
    }
}
