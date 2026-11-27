package com.streamhub.tv.feature.player

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.focusable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Text
import coil3.compose.AsyncImage
import com.streamhub.tv.core.data.model.PlaybackMarker
import com.streamhub.tv.core.data.model.UpNext
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.GlassButton
import com.streamhub.tv.core.ui.components.GlassIcons
import com.streamhub.tv.core.ui.components.PictureGlass
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.theme.GlassInk
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/*
 * The revamped player's look (approved mockup 3-player.png, v2.html): title block top-left, close
 * top-right, a wide timeline with intro/credits marked, elapsed and remaining, and one row of glass
 * buttons (the shared [GlassButton] of core/ui). The mockup is drawn at 1600 CSS px; the TV canvas
 * is 960 dp, and its sizes are taken at 0.75 so the 64 px margins land on the app's 48 dp safe area
 * and everything reads from the sofa.
 * Stateless: the screen hands in a [ControlsModel] and lambdas, so it renders in tests as it does here.
 */

internal object PlayerPalette {
    val Text = Color(0xFFF3F5FC)
    val Muted = Color(0xFFA7B0CF)
    val Kicker = Color(0xFFCFD5EA)
    val Times = Color(0xFFDFE3F2)
    val Ink = GlassInk
    val Card = Color(0xE012172A)
    val GlassLine = PictureGlass.Line
}

private val MarginX = Tokens.Canvas.SafeMarginX
private val MarginTop = 33.dp
private val MarginBottom = 33.dp
private val Button = 44.dp
private val BigButton = 58.dp

/** Where focus lands when the controls come up: the play button, or the timeline after a seek from the remote. */
enum class ControlsFocus { PlayPause, Timeline }

/** Everything the control overlay shows. */
data class ControlsModel(
    val kicker: String?,
    val title: String,
    val positionMs: Long,
    val bufferedMs: Long,
    val durationMs: Long,
    /** Left/Right is still moving the target: the time (and thumbnail) shows above the knob. */
    val seeking: Boolean,
    val thumbnailUrl: String?,
    val markers: List<PlaybackMarker>,
    val playing: Boolean,
    val seekable: Boolean,
    /** Previous / next episode buttons (episodes only; disabled when there is none that way). */
    val episodeButtons: Boolean,
    val hasPrevious: Boolean,
    val hasNext: Boolean,
    val audioLabel: String,
    val subtitleLabel: String,
    /** A line under the timeline for the cases that need saying (a room, buffered history, sleep timer). */
    val notes: List<String> = emptyList(),
)

class ControlsActions(
    val onClose: () -> Unit,
    /** Left/Right on the timeline: by this much, faster while held. */
    val onScrub: (Long) -> Unit,
    val onTogglePlay: () -> Unit,
    val onBack10: () -> Unit,
    val onForward30: () -> Unit,
    val onPrevious: () -> Unit,
    val onNext: () -> Unit,
    val onAudio: () -> Unit,
    val onSubtitles: () -> Unit,
    /**
     * "Watch together": null hides the button. Shown where the web player offers it
     * (`vm.roomStart(s) != null`); it opens the room dialog (`vm::startWatchTogether`).
     */
    val onWatchTogether: (() -> Unit)?,
    /** Picture-in-picture; null where the TV has none. */
    val onMinimise: (() -> Unit)?,
    val onSettings: () -> Unit,
    val onChannelRewind: (() -> Unit)? = null,
)

/** Focus targets outside the overlay that D-pad Up from the timeline reaches (the skip button, the Up next card). */
class ControlsLinks(val above: FocusRequester? = null)

@Composable
fun VodControls(model: ControlsModel, actions: ControlsActions, initialFocus: ControlsFocus, links: ControlsLinks, timelineFocus: FocusRequester, modifier: Modifier = Modifier) {
    val playFocus = remember { FocusRequester() }
    val closeFocus = remember { FocusRequester() }
    LaunchedEffect(Unit) {
        withFrameNanos { }
        runCatching { (if (initialFocus == ControlsFocus.Timeline && model.seekable) timelineFocus else playFocus).requestFocus() }
    }
    Box(modifier.fillMaxSize().focusGroup()) {
        // The two scrims of the mockup: the picture stays visible between them.
        Box(Modifier.fillMaxWidth().height(135.dp).background(Brush.verticalGradient(listOf(Color.Black.copy(alpha = 0.75f), Color.Transparent))))
        Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(260.dp).background(Brush.verticalGradient(listOf(Color.Transparent, Color.Black.copy(alpha = 0.88f)))))

        Column(Modifier.padding(start = MarginX, top = MarginTop, end = MarginX + Button + 24.dp)) {
            model.kicker?.let { Text(it, color = PlayerPalette.Kicker, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) }
            Text(model.title, color = Color.White, fontSize = 28.sp, fontWeight = FontWeight.ExtraBold, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 2.dp))
        }
        GlassButton(
            GlassIcons.Close, "Close", actions.onClose,
            Modifier.align(Alignment.TopEnd).padding(top = MarginTop, end = MarginX).focusRequester(closeFocus).focusProperties { down = timelineFocus },
        )

        Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().padding(start = MarginX, end = MarginX, bottom = MarginBottom)) {
            Timeline(
                model, onScrub = actions.onScrub,
                modifier = Modifier.focusRequester(timelineFocus).focusProperties {
                    up = links.above ?: closeFocus
                    down = playFocus
                },
            )
            Row(Modifier.fillMaxWidth().padding(top = 6.dp)) {
                Text(clock(model.positionMs), color = PlayerPalette.Times, fontSize = 14.sp)
                Spacer(Modifier.weight(1f))
                model.notes.firstOrNull()?.let { Text(it, color = PlayerPalette.Muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center) }
                Spacer(Modifier.weight(1f))
                if (model.durationMs > 0) Text("−" + clock(model.durationMs - model.positionMs), color = PlayerPalette.Times, fontSize = 14.sp)
            }
            ControlRow(model, actions, playFocus, timelineFocus, Modifier.padding(top = 10.dp))
        }
    }
}

@Composable
private fun ControlRow(model: ControlsModel, actions: ControlsActions, playFocus: FocusRequester, timelineFocus: FocusRequester, modifier: Modifier) {
    val upToTimeline = Modifier.focusProperties { up = timelineFocus }
    Box(modifier.fillMaxWidth().height(BigButton)) {
        Row(Modifier.align(Alignment.CenterStart), verticalAlignment = Alignment.CenterVertically) {
            GlassButton(GlassIcons.Audio, "Audio: ${model.audioLabel}", actions.onAudio, upToTimeline)
            Text(model.audioLabel, color = PlayerPalette.Muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(start = 10.dp).widthIn(max = 150.dp))
            GlassButton(GlassIcons.Subtitles, "Subtitles: ${model.subtitleLabel}", actions.onSubtitles, upToTimeline.padding(start = 18.dp))
            Text(model.subtitleLabel, color = PlayerPalette.Muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(start = 10.dp).widthIn(max = 120.dp))
        }
        Row(Modifier.align(Alignment.Center), horizontalArrangement = Arrangement.spacedBy(11.dp), verticalAlignment = Alignment.CenterVertically) {
            if (model.episodeButtons) GlassButton(GlassIcons.Previous, "Previous episode", actions.onPrevious, upToTimeline, enabled = model.hasPrevious)
            GlassButton(GlassIcons.Back, "Back 10 seconds", actions.onBack10, upToTimeline, enabled = model.seekable, badge = "10")
            GlassButton(
                if (model.playing) GlassIcons.Pause else GlassIcons.Play, if (model.playing) "Pause" else "Play", actions.onTogglePlay,
                upToTimeline.focusRequester(playFocus), size = BigButton, primary = true,
            )
            GlassButton(GlassIcons.Forward, "Forward 30 seconds", actions.onForward30, upToTimeline, enabled = model.seekable, badge = "30")
            if (model.episodeButtons) GlassButton(GlassIcons.Next, "Next episode", actions.onNext, upToTimeline, enabled = model.hasNext)
        }
        Row(Modifier.align(Alignment.CenterEnd), horizontalArrangement = Arrangement.spacedBy(11.dp), verticalAlignment = Alignment.CenterVertically) {
            actions.onChannelRewind?.let { GlassButton(GlassIcons.History, "Rewind this channel", it, upToTimeline) }
            actions.onWatchTogether?.let { GlassButton(GlassIcons.People, "Watch together", it, upToTimeline) }
            actions.onMinimise?.let { GlassButton(GlassIcons.Minimise, "Mini player", it, upToTimeline) }
            GlassButton(GlassIcons.Settings, "Settings", actions.onSettings, upToTimeline)
        }
    }
}

/**
 * The wide timeline: watched in the accent, buffered lighter, intro and credits marked, the knob
 * at the position. Focusable: Left/Right seek (faster while held); while a seek is under way the
 * time, and the scrub thumbnail when the title has them, ride above the knob.
 */
@Composable
private fun Timeline(model: ControlsModel, onScrub: (Long) -> Unit, modifier: Modifier) {
    val t = LocalThemeTokens.current
    val source = remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val duration = model.durationMs.coerceAtLeast(0)
    val fraction = if (duration > 0) (model.positionMs.toFloat() / duration).coerceIn(0f, 1f) else 0f
    val buffered = if (duration > 0) (model.bufferedMs.toFloat() / duration).coerceIn(0f, 1f) else 0f
    val knob by animateDpAsState(if (focused) 20.dp else 15.dp, tween(Tokens.Focus.DURATION_MS), label = "knob")
    BoxWithConstraints(
        modifier
            .fillMaxWidth()
            .height(150.dp)
            .onPreviewKeyEvent { e ->
                val direction = when (e.key) { Key.DirectionLeft -> -1; Key.DirectionRight -> 1; else -> 0 }
                if (direction == 0) false else {
                    if (e.type == KeyEventType.KeyDown && model.seekable) onScrub(direction * PlayerKeyMap.seekStepMs(e.nativeKeyEvent.repeatCount))
                    // Consumed at either end too: nothing beside the timeline to move to.
                    true
                }
            }
            .semantics { contentDescription = "Timeline, ${clock(model.positionMs)} of ${clock(duration)}" }
            .focusable(interactionSource = source),
    ) {
        val width = maxWidth
        val trackHeight = if (focused) 7.dp else 5.dp
        // The track and its marks, drawn at the bottom of the box.
        Canvas(Modifier.align(Alignment.BottomStart).fillMaxWidth().height(24.dp)) {
            val h = trackHeight.toPx()
            val y = size.height / 2 - h / 2
            val r = CornerRadius(h / 2, h / 2)
            drawRoundRect(Color.White.copy(alpha = 0.25f), Offset(0f, y), Size(size.width, h), r)
            if (buffered > 0f) drawRoundRect(Color.White.copy(alpha = 0.35f), Offset(0f, y), Size(size.width * buffered, h), r)
            if (duration > 0) model.markers.forEach { m ->
                val from = (m.startMs.toFloat() / duration).coerceIn(0f, 1f) * size.width
                val to = (m.endMs.toFloat() / duration).coerceIn(0f, 1f) * size.width
                val mh = h + 8.dp.toPx()
                if (to > from) drawRoundRect(Color.White.copy(alpha = 0.55f), Offset(from, size.height / 2 - mh / 2), Size(to - from, mh), CornerRadius(3.dp.toPx()))
            }
            drawRoundRect(t.accent, Offset(0f, y), Size(size.width * fraction, h), r)
            val cx = size.width * fraction
            val k = knob.toPx()
            drawCircle(t.accent.copy(alpha = 0.45f), radius = k / 2 + 4.5.dp.toPx(), center = Offset(cx, size.height / 2))
            drawCircle(Color.White, radius = k / 2, center = Offset(cx, size.height / 2))
        }
        if (model.seeking && duration > 0) {
            val bubble = if (model.thumbnailUrl != null) 176.dp else 72.dp
            val x = (width * fraction - bubble / 2).coerceIn(0.dp, (width - bubble).coerceAtLeast(0.dp))
            Column(Modifier.align(Alignment.BottomStart).offset(x = x, y = (-26).dp).width(bubble), horizontalAlignment = Alignment.CenterHorizontally) {
                model.thumbnailUrl?.let { url ->
                    AsyncImage(
                        url, null, contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f).shadow(12.dp, RoundedCornerShape(8.dp)).clip(RoundedCornerShape(8.dp))
                            .background(Color.Black).border(2.dp, Color.White, RoundedCornerShape(8.dp)),
                    )
                }
                Text(clock(model.positionMs), color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 4.dp))
            }
        }
    }
}

/** "Skip intro" / "Skip credits": white, accent-ringed, bottom-right. OK activates it while the controls are hidden. */
@Composable
fun SkipButton(label: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    val source = remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val scale by animateFloatAsState(if (focused) 1.06f else 1f, tween(Tokens.Focus.DURATION_MS), label = "skipScale")
    Row(
        modifier
            .scale(scale)
            .height(44.dp)
            .shadow(10.dp, RoundedCornerShape(10.dp))
            .clip(RoundedCornerShape(10.dp))
            .background(Color.White.copy(alpha = 0.95f))
            .border(if (focused) 3.5.dp else 2.5.dp, t.accent, RoundedCornerShape(10.dp))
            .clickable(interactionSource = source, indication = null, onClick = onClick)
            .padding(horizontal = 18.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(label, color = PlayerPalette.Ink, fontSize = 15.sp, fontWeight = FontWeight.Bold)
        Image(GlassIcons.SkipForward, null, Modifier.size(17.dp), colorFilter = ColorFilter.tint(PlayerPalette.Ink))
    }
}

/** What the Up next card shows. */
data class UpNextCardModel(
    val next: UpNext,
    /** Whole seconds left, and the share of the countdown run (the ring); null without a countdown (autoplay off). */
    val secondsLeft: Int?,
    val progress: Float,
)

/**
 * The Up next card: the next episode's still with the countdown ring, "Up next · S2 · E2", its
 * title and what happens next. With autoplay off there is no ring; a "Play next" pill says OK plays it.
 */
@Composable
fun UpNextCard(model: UpNextCardModel, onPlay: () -> Unit, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    val source = remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val ring by animateFloatAsState(model.progress, tween(260, easing = LinearEasing), label = "ring")
    Row(
        modifier
            .width(330.dp)
            .shadow(18.dp, RoundedCornerShape(12.dp))
            .clip(RoundedCornerShape(12.dp))
            .background(PlayerPalette.Card)
            .border(if (focused) 3.dp else 1.dp, if (focused) t.accent else PlayerPalette.GlassLine, RoundedCornerShape(12.dp))
            .clickable(interactionSource = source, indication = null, onClick = onPlay)
            .padding(10.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(132.dp).aspectRatio(16f / 9f).clip(RoundedCornerShape(8.dp)).background(Color(0xFF1C2433)), contentAlignment = Alignment.Center) {
            model.next.image?.let { AsyncImage(it, null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize()) }
            if (model.secondsLeft != null) {
                Box(Modifier.size(42.dp).clip(CircleShape).background(Color.Black.copy(alpha = 0.55f)), contentAlignment = Alignment.Center) {
                    Canvas(Modifier.fillMaxSize().padding(1.5.dp)) {
                        val w = 2.5.dp.toPx()
                        drawArc(Color.White.copy(alpha = 0.25f), 0f, 360f, false, style = Stroke(w))
                        drawArc(t.accent, -90f, 360f * (1f - ring), false, style = Stroke(w, cap = StrokeCap.Round))
                    }
                    Text(model.secondsLeft.toString(), color = Color.White, fontSize = 17.sp, fontWeight = FontWeight.ExtraBold)
                }
            }
        }
        Column(Modifier.weight(1f)) {
            Text(listOf("Up next", model.next.subtitle).filter { it.isNotBlank() }.joinToString(" · "), color = PlayerPalette.Muted, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(model.next.title, color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 1.dp, bottom = 4.dp))
            if (model.secondsLeft != null) {
                Text("Plays in ${model.secondsLeft} s · press Back to keep watching", color = PlayerPalette.Muted, fontSize = 12.sp, lineHeight = 16.sp, maxLines = 2)
            } else {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Row(
                        Modifier.clip(RoundedCornerShape(7.dp)).background(Color.White).padding(horizontal = 10.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Image(GlassIcons.Play, null, Modifier.size(11.dp), colorFilter = ColorFilter.tint(PlayerPalette.Ink))
                        Text("Play next", color = PlayerPalette.Ink, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                    }
                    Text("Press OK", color = PlayerPalette.Muted, fontSize = 12.sp, modifier = Modifier.padding(start = 8.dp))
                }
            }
        }
    }
}

/**
 * Still watching? after three episodes started by autoplay with nobody touching the remote. The
 * picture is paused behind it; focus starts on carrying on.
 */
@Composable
fun StillWatchingPanel(next: UpNext?, onContinue: () -> Unit, onBack: () -> Unit, modifier: Modifier = Modifier) {
    val first = remember { FocusRequester() }
    LaunchedEffect(Unit) { withFrameNanos { }; runCatching { first.requestFocus() } }
    Box(modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.72f)).focusGroup(), contentAlignment = Alignment.Center) {
        Column(
            Modifier.width(470.dp).clip(RoundedCornerShape(16.dp)).background(PlayerPalette.Card).border(1.dp, PlayerPalette.GlassLine, RoundedCornerShape(16.dp)).padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("Still watching?", color = Color.White, fontSize = 28.sp, fontWeight = FontWeight.ExtraBold)
            Text(
                next?.let { "Up next: " + listOf(it.subtitle, it.title).filter { s -> s.isNotBlank() }.joinToString(" · ") } ?: "Playback is paused.",
                color = PlayerPalette.Muted, fontSize = 15.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
            )
            Row(Modifier.padding(top = 14.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                ShButton("Continue watching", onContinue, Modifier.focusRequester(first), style = ButtonStyle.Primary)
                ShButton("Back to the show", onBack)
            }
        }
    }
}
