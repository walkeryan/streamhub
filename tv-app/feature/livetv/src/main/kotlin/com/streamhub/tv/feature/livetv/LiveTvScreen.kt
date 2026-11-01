package com.streamhub.tv.feature.livetv

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
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
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Icon
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.data.model.ChannelGroup
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.ProgressLine
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.components.ShTextField
import com.streamhub.tv.core.ui.components.programmeProgress
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalReducedMotion
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

private val HHMM = DateTimeFormatter.ofPattern("HH:mm")
internal fun hhmm(iso: String): String = runCatching { ZonedDateTime.parse(iso).withZoneSameInstant(ZoneId.systemDefault()).format(HHMM) }.getOrDefault("")

@Composable
fun LiveTvScreen(vm: LiveTvViewModel = hiltViewModel(), filtersOnly: Boolean = false) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val launcher = LocalPlayLauncher.current
    val rail = LocalRailController.current
    DisposableEffect(Unit) { onDispose { vm.stopPreview() } }
    var groupsOpen by remember { mutableStateOf(false) }
    var restoreGroupFocus by remember { mutableStateOf(false) }
    val groupOpener = remember { FocusRequester() }
    val firstChannel = remember { FocusRequester() }
    val channelListState = rememberLazyListState()
    var enterChannelsWhenReady by remember { mutableStateOf(false) }
    LaunchedEffect(s.loading, s.error, s.channels.firstOrNull()?.id, enterChannelsWhenReady) {
        if (!filtersOnly && enterChannelsWhenReady && !s.loading) {
            if (s.channels.isNotEmpty() && s.error == null) channelListState.scrollToItem(1)
            withFrameNanos { }
            firstChannel.requestFocus()
            enterChannelsWhenReady = false
        }
    }
    val choices = remember(s.groups) { groupChoices(s.groups) }
    LaunchedEffect(groupsOpen) {
        if (!groupsOpen && restoreGroupFocus) {
            withFrameNanos { }
            groupOpener.requestFocus()
            restoreGroupFocus = false
        }
    }
    if (groupsOpen) GroupDrawer(
        groups = s.groups, selected = s.selectedGroup,
        loading = s.groupsLoading, error = s.groupsError,
        onRetry = vm::refreshGroups,
        onDismiss = { groupsOpen = false },
        onSelect = { vm.selectGroup(it); groupsOpen = false },
    )


    Column((if (filtersOnly) Modifier.fillMaxWidth() else Modifier.fillMaxSize()).padding(vertical = if (filtersOnly) 0.dp else Tokens.Canvas.SafeMarginY)) {
        // Language chips (profile languages + All) and favorites filter.
        LazyRow(
            Modifier.fillMaxWidth().focusRestorer(),
            contentPadding = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            item("fav") {
                ShChip("Favorites", s.favoritesOnly, vm::toggleFavoritesOnly, Modifier.leftOpensRail(rail), leading = Icons.Filled.Favorite)
            }
            item("all") { ShChip("All languages", s.selectedLanguages == null, { vm.toggleLanguage(null) }) }
            val shown = (s.profileLanguages + s.languages.map { it.code }).distinct()
            items(shown, key = { "l-$it" }) { code ->
                val name = s.languages.firstOrNull { it.code == code }?.name ?: code.uppercase()
                ShChip(name, s.selectedLanguages?.contains(code) == true, { vm.toggleLanguage(code) })
            }
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            ShButton("Groups: ${s.selectedGroup ?: "All groups"}", {
                restoreGroupFocus = true
                groupsOpen = true
                vm.stopPreview()
            }, Modifier.width(360.dp).focusRequester(groupOpener).leftOpensRail(rail)
                .onPreviewKeyEvent { event ->
                    if (!filtersOnly && (s.loading || s.channels.isNotEmpty()) && event.key == Key.DirectionDown && event.type == KeyEventType.KeyDown) {
                        enterChannelsWhenReady = true
                        true
                    } else false
                })
            Text(if (s.groupsLoading) "Updating groups…" else if (s.groupsError != null) "Group counts unavailable" else {
                val count = if (s.selectedGroup == null) choices.sumOf { it.channelCount } else choices.firstOrNull { it.name == s.selectedGroup }?.channelCount ?: 0
                "$count channels · ${choices.size} groups"
            }, color = t.textMuted, style = MaterialTheme.typography.labelMedium)
        }

        s.notice?.let { Text(it, color = t.textMuted, modifier = Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX)) }
        if (!filtersOnly) Row(Modifier.weight(1f).fillMaxWidth().padding(top = 16.dp)) {
            // Channel list (left)
            Box(Modifier.width(440.dp).fillMaxHeight().padding(start = Tokens.Canvas.SafeMarginX)) {
                when {
                    s.loading && s.channels.isEmpty() -> Text("Loading channels…", color = t.textMuted, style = MaterialTheme.typography.bodyLarge)
                    s.error != null -> Column { Text("Couldn't load channels", color = t.danger); ShButton("Retry", { enterChannelsWhenReady = true; vm.load(s.offset) }, Modifier.focusRequester(firstChannel)) }
                    s.channels.isEmpty() -> Column { Text(if (s.favoritesOnly) "No favorite channels yet. Choose a channel, then select Add favorite in its details." else "No channels match these filters.", color = t.textMuted, style = MaterialTheme.typography.bodyLarge); ShButton("Change filters", { groupOpener.requestFocus() }, Modifier.focusRequester(firstChannel)) }
                    else -> PivotScroll(leadingOffset = 72.dp) {
                        LazyColumn(
                            state = channelListState,
                            modifier = Modifier.fillMaxSize().focusRestorer(),
                            verticalArrangement = Arrangement.spacedBy(6.dp),
                            contentPadding = PaddingValues(vertical = 8.dp, horizontal = 8.dp),
                        ) {
                            item("page-navigation") {
                                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                    if (s.offset > 0) ShButton("Previous 60", { enterChannelsWhenReady = true; vm.previousPage() })
                                    if (s.offset + 60 < s.total) ShButton("Next 60", { enterChannelsWhenReady = true; vm.nextPage() })
                                }
                            }
                            itemsIndexed(s.channels, key = { _, c -> c.id }) { index, ch ->
                                ChannelRow(
                                    ch,
                                    playing = ch.id == s.previewChannelId,
                                    onClick = { launcher.play(ch.id) },
                                    onLongClick = { vm.toggleFavorite(ch) },
                                    modifier = Modifier
                                        .then(if (index == 0) Modifier.focusRequester(firstChannel) else Modifier)
                                        .leftOpensRail(rail).onFocusChanged { if (it.isFocused) vm.onChannelFocused(ch) },
                                )
                            }
                        }
                    }
                }
            }
            // Preview (right)
            Column(Modifier.weight(1f).padding(start = 24.dp, end = Tokens.Canvas.SafeMarginX)) {
                Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f).clip(RoundedCornerShape(Tokens.Radius.Md)).background(androidx.compose.ui.graphics.Color.Black)) {
                    val focusedChannel = s.channels.firstOrNull { it.id == s.previewChannelId }
                    if (focusedChannel?.logo != null) AsyncImage(focusedChannel.logo, focusedChannel.name,
                        contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize().padding(32.dp))
                    if (s.previewChannelId == null) {
                        Text("Focus a channel for details", color = t.textMuted, modifier = Modifier.align(Alignment.Center), style = MaterialTheme.typography.bodyLarge)
                    }
                }
                val ch = s.channels.firstOrNull { it.id == s.previewChannelId }
                if (ch != null) {
                    Text("${ch.number}  ${ch.name}", style = MaterialTheme.typography.titleMedium, color = t.text, modifier = Modifier.padding(top = 12.dp))
                    ch.now?.let { Text("Now  ${hhmm(it.start)}–${hhmm(it.stop)}  ${it.title}", style = MaterialTheme.typography.bodyMedium, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                    ch.now?.description?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = t.textMuted, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp)) }
                    ch.next?.let { Text("Next ${hhmm(it.start)}  ${it.title}", style = MaterialTheme.typography.bodyMedium, color = t.textMuted, maxLines = 1, modifier = Modifier.padding(top = 4.dp)) }
                    Row(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        ShButton("Watch", { launcher.play(ch.id) })
                        ShButton(if (ch.isFavorite) "Remove favorite" else "Add favorite", { vm.toggleFavorite(ch) })
                    }
                }
            }
        }
    }
}

@Composable
private fun ChannelRow(ch: Channel, playing: Boolean, onClick: () -> Unit, onLongClick: () -> Unit, modifier: Modifier) {
    val t = LocalThemeTokens.current
    FocusSurface(
        onClick = onClick,
        onLongClick = onLongClick,
        modifier = modifier.fillMaxWidth().height(64.dp),
        containerColor = if (playing) t.surfaceAlt else t.surface,
        focusedContainerColor = t.surfaceAlt,
        scaleOnFocus = false,
        glow = false,
    ) {
        Row(Modifier.fillMaxSize().padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(ch.number.toString(), style = MaterialTheme.typography.labelLarge, color = t.textMuted, modifier = Modifier.width(44.dp))
            Box(Modifier.size(44.dp), contentAlignment = Alignment.Center) {
                if (ch.logo != null) AsyncImage(ch.logo, ch.name, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize())
                else Text(ch.name.take(2).uppercase(), color = t.text, style = MaterialTheme.typography.labelLarge)
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(ch.name, style = MaterialTheme.typography.labelLarge, color = t.text, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                    if (ch.isFavorite) Icon(Icons.Filled.Favorite, "Favorite", tint = t.accent, modifier = Modifier.padding(start = 6.dp).size(14.dp))
                    if (playing) Text("  ▶", color = t.accent, style = MaterialTheme.typography.labelSmall)
                }
                Text(ch.now?.title ?: "—", style = MaterialTheme.typography.labelSmall, color = t.textMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                programmeProgress(ch.now)?.let { ProgressLine(it, Modifier.padding(top = 3.dp), height = 2.dp) }
            }
        }
    }
}


/** Separate modal focus window: D-pad cannot escape into the channel list behind the drawer. */
@Composable
private fun GroupDrawer(
    groups: List<ChannelGroup>, selected: String?, loading: Boolean, error: String?,
    onRetry: () -> Unit, onDismiss: () -> Unit, onSelect: (String?) -> Unit,
) {
    val t = LocalThemeTokens.current
    var query by remember { mutableStateOf("") }
    val all = remember(groups) { groupChoices(groups) }
    val matches = remember(groups, query) { groupChoices(groups, query) }
    val first = remember { FocusRequester() }
    val listState = rememberLazyListState()
    var entered by remember { mutableStateOf(false) }
    val reducedMotion = LocalReducedMotion.current
    val slide by animateFloatAsState(if (entered) 0f else -1f, tween(if (reducedMotion) 0 else 180), label = "groupDrawerSlide")
    LaunchedEffect(Unit) { entered = true; withFrameNanos { }; first.requestFocus() }
    LaunchedEffect(query) { listState.scrollToItem(0) }
    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Box(Modifier.fillMaxSize()) {
            Column(Modifier.align(Alignment.CenterStart).width(460.dp).fillMaxHeight()
                .graphicsLayer { translationX = slide * size.width }
                .background(t.surface.copy(alpha = 0.97f), RoundedCornerShape(topEnd = 20.dp, bottomEnd = 20.dp))
                .border(1.dp, t.textMuted.copy(alpha = 0.2f), RoundedCornerShape(topEnd = 20.dp, bottomEnd = 20.dp))
                .padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Text("Channel groups", style = MaterialTheme.typography.titleLarge, color = t.text)
                    ShButton("Close", onDismiss)
                }
                ShTextField(query, { query = it }, "Search groups", Modifier.fillMaxWidth(), placeholder = "Sports, news, movies…")
                ShButton(if (selected == null) "✓ All groups" else "All groups", { onSelect(null) }, Modifier.fillMaxWidth().focusRequester(first))
                when {
                    loading -> Text("Updating group counts…", color = t.textMuted)
                    error != null -> { Text("Couldn't load groups", color = t.danger); ShButton("Retry", onRetry) }
                    else -> Text("${matches.size} of ${all.size} groups · current language and favorites filters", color = t.textMuted, style = MaterialTheme.typography.labelSmall)
                }
                LazyColumn(state = listState, modifier = Modifier.fillMaxWidth().weight(1f),
                    verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = PaddingValues(4.dp)) {
                    items(matches, key = { it.name }) { group ->
                        FocusSurface(onClick = { onSelect(group.name) }, modifier = Modifier.fillMaxWidth().height(60.dp),
                            containerColor = if (selected == group.name) t.surfaceAlt else t.surface,
                            focusedContainerColor = t.text, scaleOnFocus = false) { focused ->
                            Row(Modifier.fillMaxSize().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text((if (selected == group.name) "✓ " else "") + group.name,
                                    Modifier.weight(1f), color = if (focused) t.bg else t.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(if (loading || error != null) "—" else group.channelCount.toString(), color = if (focused) t.bg else t.textMuted,
                                    modifier = Modifier.padding(start = 10.dp))
                            }
                        }
                    }
                    if (matches.isEmpty() && !loading && error == null) item { Text("No matching groups. Try another search.", color = t.textMuted) }
                }
            }
        }
    }
}
