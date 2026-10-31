package com.streamhub.tv.feature.guide

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.CenterMessage
import com.streamhub.tv.core.ui.components.LoadingView
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShTextField
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.delay
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val HHMM = DateTimeFormatter.ofPattern("HH:mm").withZone(ZoneId.systemDefault())

/** 30 minutes = 180 dp → 2 h visible in the ~720 dp grid viewport (960 dp design minus rail/channel column). */
private val SLOT_WIDTH = 180.dp
private val ROW_HEIGHT = 60.dp
private val CHANNEL_COL = 200.dp

private fun Duration.toDp(): Dp = SLOT_WIDTH * (toMinutes() / 30f)

/**
 * Classic horizontal EPG. All rows share one horizontal ScrollState so time columns stay aligned;
 * D-pad Left/Right moves between programme cells (focus search brings them into view), Up/Down
 * moves between channels. OK on a live programme plays the channel; OK/long-press on a past
 * programme with catch_up_available offers Catch-up.
 */
@Composable
fun GuideScreen(vm: GuideViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val launcher = LocalPlayLauncher.current
    val hScroll = rememberScrollState()
    val density = LocalDensity.current
    var catchup by remember { mutableStateOf<Pair<Channel, GuideProgramme>?>(null) }
    val now by produceState(Instant.now()) { while (true) { delay(30_000); value = Instant.now() } }

    // Start scrolled so 'now' is near the left edge (one slot of past visible).
    LaunchedEffect(s.windowStart, s.loading) {
        if (!s.loading) {
            val off = Duration.between(s.windowStart, now.minus(Duration.ofMinutes(30))).toDp()
            hScroll.scrollTo(with(density) { off.roundToPx() }.coerceAtLeast(0))
        }
    }

    val totalWidth = Duration.between(s.windowStart, s.windowEnd).toDp()
    Column(Modifier.fillMaxSize().padding(vertical = Tokens.Canvas.SafeMarginY)) {
        Column(Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                ShChip("With schedules", s.scheduledOnly, { vm.schedulesOnly(true) }, Modifier.leftOpensRail(LocalRailController.current))
                ShChip("All channels", !s.scheduledOnly, { vm.schedulesOnly(false) })
                ShButton("Previous", { vm.page(-1) }, enabled = s.page > 0 && !s.loading)
                ShButton("Next", { vm.page(1) }, enabled = !s.loading && (s.total?.let { (s.page + 1) * GuideViewModel.PAGE_SIZE < it } ?: (s.channels.size == GuideViewModel.PAGE_SIZE)))
                Text(s.total?.let { "${s.page * GuideViewModel.PAGE_SIZE + if (s.channels.isEmpty()) 0 else 1}–${s.page * GuideViewModel.PAGE_SIZE + s.channels.size} of $it" } ?: "Page ${s.page + 1}", color = t.textMuted)
            }
            ShTextField(s.query, vm::search, "Search guide channels", Modifier.fillMaxWidth(), placeholder = "Channel name or number")
        }
        when {
            s.loading -> { LoadingView(); return@Column }
            s.error != null -> { CenterMessage("Couldn't load the guide", s.error) { ShButton("Retry", { vm.load() }, style = ButtonStyle.Primary) }; return@Column }
            s.channels.isEmpty() -> {
                CenterMessage("No matching channels", if (s.scheduledOnly) "No schedules match this search and your profile languages. Try All channels." else "Try a different channel name or number.")
                return@Column
            }
        }
        // Details header for the focused programme
        Column(Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX).height(96.dp)) {
            val sel = s.selected
            Text(sel?.p?.title ?: s.selectedChannel?.name.orEmpty(), style = MaterialTheme.typography.titleLarge, color = t.text, maxLines = 1)
            if (sel != null) {
                Text(
                    "${s.selectedChannel?.name.orEmpty()}  ·  ${HHMM.format(sel.start)}–${HHMM.format(sel.stop)}" +
                        (sel.p.category?.let { "  ·  $it" } ?: "") + if (sel.p.catchupAvailable && sel.stop < now) "  ·  Catch-up available" else "",
                    style = MaterialTheme.typography.bodyMedium, color = t.textMuted, maxLines = 1,
                )
                sel.p.description?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = t.textMuted, maxLines = 2, overflow = TextOverflow.Ellipsis) }
            }
        }
        // Time ruler
        Row(Modifier.fillMaxWidth().padding(start = Tokens.Canvas.SafeMarginX)) {
            Box(Modifier.width(CHANNEL_COL))
            Box(Modifier.weight(1f).horizontalScroll(hScroll, enabled = false)) {
                Box(Modifier.width(totalWidth).height(28.dp)) {
                    var slot = s.windowStart
                    while (slot < s.windowEnd) {
                        val x = Duration.between(s.windowStart, slot).toDp()
                        Text(HHMM.format(slot), style = MaterialTheme.typography.labelMedium, color = t.textMuted, modifier = Modifier.offset(x = x + 6.dp))
                        slot = slot.plus(Duration.ofMinutes(30))
                    }
                    NowLine(Duration.between(s.windowStart, now).toDp())
                }
            }
        }
        PivotScroll(leadingOffset = ROW_HEIGHT) {
            LazyColumn(Modifier.fillMaxSize().padding(start = Tokens.Canvas.SafeMarginX).focusRestorer()) {
                items(s.channels, key = { it.id }) { ch ->
                    Row(Modifier.fillMaxWidth().height(ROW_HEIGHT).padding(vertical = 2.dp)) {
                        ChannelCell(ch)
                        Box(Modifier.weight(1f).fillMaxHeight().horizontalScroll(hScroll)) {
                            Box(Modifier.width(totalWidth).fillMaxHeight()) {
                                val progs = s.programmes[ch.id].orEmpty()
                                if (progs.isEmpty()) {
                                    ProgrammeCell("No schedule supplied", 0.dp, totalWidth, live = true, past = false, catchup = false,
                                        modifier = Modifier.leftOpensRail(LocalRailController.current),
                                        onFocus = { vm.select(ch, null) }, onClick = { launcher.play(ch.id) })
                                }
                                progs.forEachIndexed { i, gp ->
                                    val start = maxOf(gp.start, s.windowStart)
                                    val x = Duration.between(s.windowStart, start).toDp()
                                    val w = Duration.between(start, minOf(gp.stop, s.windowEnd)).toDp()
                                    val live = gp.start <= now && gp.stop > now
                                    val past = gp.stop <= now
                                    ProgrammeCell(
                                        gp.p.title, x, w, live, past, gp.p.catchupAvailable,
                                        modifier = if (i == 0) Modifier.leftOpensRail(LocalRailController.current) else Modifier,
                                        onFocus = { vm.select(ch, gp) },
                                        onClick = {
                                            when {
                                                live -> launcher.play(ch.id)
                                                past && gp.p.catchupAvailable -> catchup = ch to gp
                                                else -> Unit // future: nothing to play yet
                                            }
                                        },
                                        onLongClick = { if (past && gp.p.catchupAvailable) catchup = ch to gp },
                                    )
                                }
                                NowLine(Duration.between(s.windowStart, now).toDp())
                            }
                        }
                    }
                }
            }
        }
    }

    catchup?.let { (ch, gp) ->
        Dialog(onDismissRequest = { catchup = null }) {
            Column(Modifier.background(t.surface, RoundedCornerShape(Tokens.Radius.Lg)).padding(28.dp).width(460.dp)) {
                Text(gp.p.title, style = MaterialTheme.typography.titleLarge, color = t.text)
                Text("${ch.name} · ${HHMM.format(gp.start)}–${HHMM.format(gp.stop)}", style = MaterialTheme.typography.bodyMedium, color = t.textMuted)
                Row(Modifier.padding(top = 20.dp)) {
                    ShButton("Watch from start (Catch-up)", {
                        launcher.play(ch.id, catchupProgrammeId = gp.p.id); catchup = null
                    }, style = ButtonStyle.Primary)
                    Box(Modifier.width(12.dp))
                    ShButton("Cancel", { catchup = null })
                }
            }
        }
    }
}

@Composable
private fun NowLine(x: Dp) {
    val t = LocalThemeTokens.current
    Box(Modifier.offset(x = x).width(2.dp).fillMaxHeight().background(t.accent))
}

@Composable
private fun ChannelCell(ch: Channel) {
    val t = LocalThemeTokens.current
    Row(Modifier.width(CHANNEL_COL).fillMaxHeight().background(t.surface, RoundedCornerShape(Tokens.Radius.Sm)).padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(ch.number.toString(), style = MaterialTheme.typography.labelMedium, color = t.textMuted, modifier = Modifier.width(36.dp))
        if (ch.logo != null) AsyncImage(ch.logo, null, contentScale = ContentScale.Fit, modifier = Modifier.size(32.dp))
        Text(ch.name, style = MaterialTheme.typography.labelLarge, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(start = 8.dp))
    }
}

@Composable
private fun ProgrammeCell(
    title: String, x: Dp, w: Dp, live: Boolean, past: Boolean, catchup: Boolean,
    modifier: Modifier = Modifier,
    onFocus: () -> Unit, onClick: () -> Unit, onLongClick: (() -> Unit)? = null,
) {
    val t = LocalThemeTokens.current
    FocusSurface(
        onClick = onClick,
        onLongClick = onLongClick,
        modifier = modifier.offset(x = x).width((w - 2.dp).coerceAtLeast(4.dp)).fillMaxHeight().padding(start = 2.dp)
            .onFocusChanged { if (it.isFocused) onFocus() },
        shape = RoundedCornerShape(Tokens.Radius.Sm),
        containerColor = if (live) t.surfaceAlt else t.surface,
        focusedContainerColor = t.text,
        scaleOnFocus = false,
        glow = false,
    ) { focused ->
        val fg = when { focused -> t.bg; past -> t.textMuted; else -> t.text }
        Row(Modifier.align(Alignment.CenterStart).padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            if (past && catchup) Text("↺ ", color = fg, style = MaterialTheme.typography.labelMedium)
            Text(title, color = fg, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}
