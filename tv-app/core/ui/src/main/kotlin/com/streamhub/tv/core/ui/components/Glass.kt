package com.streamhub.tv.core.ui.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.theme.GlassInk
import com.streamhub.tv.core.ui.theme.LocalReducedMotion
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import com.streamhub.tv.core.ui.theme.glass

/*
 * The shared pieces of the glass look (see theme/Glass.kt): the round glass button of the player,
 * the panel every dialog and drawer sits on, the 10-foot type of screens and dialogs, and the list
 * row of menus and pickers. Focus is always a ring in the theme's accent plus a lift or a fill,
 * never colour alone.
 */

/** Glass over a picture (the player): fixed white tints, whatever the theme, because video is behind. */
object PictureGlass {
    val Line = Color.White.copy(alpha = 0.18f)
    val Top = Color.White.copy(alpha = 0.16f)
    val Bottom = Color.White.copy(alpha = 0.05f)
}

private val GlassButtonSize = 44.dp

/**
 * The glass circle of the player mockup. Focus is an accent ring and a slight lift (never colour
 * alone); [primary] is the large white play/pause. Disabled buttons are dimmed and skipped by the D-pad.
 */
@Composable
fun GlassButton(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    size: Dp = GlassButtonSize,
    primary: Boolean = false,
    enabled: Boolean = true,
    badge: String? = null,
) {
    val t = LocalThemeTokens.current
    val source = remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val scale by animateFloatAsState(if (focused) 1.12f else 1f, tween(Tokens.Focus.DURATION_MS), label = "glassScale")
    val ink = if (primary) GlassInk else Color.White
    val fill = when {
        primary -> Brush.verticalGradient(listOf(Color.White, Color.White))
        focused -> Brush.verticalGradient(listOf(Color.White.copy(alpha = 0.34f), Color.White.copy(alpha = 0.18f)))
        else -> Brush.verticalGradient(listOf(PictureGlass.Top, PictureGlass.Bottom))
    }
    Box(
        modifier
            .scale(scale)
            .size(size)
            .shadow(if (focused) 14.dp else 6.dp, CircleShape, ambientColor = if (focused) t.accent else Color.Black, spotColor = if (focused) t.accent else Color.Black)
            .clip(CircleShape)
            .background(fill)
            .border(if (focused) 3.dp else 1.dp, if (focused) t.accent else PictureGlass.Line, CircleShape)
            .semantics { contentDescription = label }
            .clickable(interactionSource = source, indication = null, enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        val alpha = if (enabled) 1f else 0.35f
        Image(icon, null, Modifier.size(if (primary) size * 0.42f else size * 0.5f), colorFilter = ColorFilter.tint(ink.copy(alpha = alpha)))
        if (badge != null) Text(badge, color = ink.copy(alpha = alpha), fontSize = 7.sp, fontWeight = FontWeight.Bold, modifier = Modifier.offset(y = 1.5.dp))
    }
}

val PanelShape = RoundedCornerShape(18.dp)

/**
 * The glass sheet under a dialog, the settings drawer or a menu: the theme's panel colour, a faint
 * light from the top, a hairline border with a brighter top edge, and a soft shadow.
 */
@Composable
fun GlassPanel(
    modifier: Modifier = Modifier,
    shape: Shape = PanelShape,
    padding: PaddingValues = PaddingValues(32.dp),
    horizontalAlignment: Alignment.Horizontal = Alignment.Start,
    verticalArrangement: Arrangement.Vertical = Arrangement.spacedBy(14.dp),
    content: @Composable ColumnScope.() -> Unit,
) {
    val g = glass
    Column(
        modifier
            .shadow(28.dp, shape, ambientColor = Color.Black, spotColor = Color.Black)
            .clip(shape)
            .background(g.panel)
            .background(Brush.verticalGradient(0f to g.highlight, 0.4f to Color.Transparent))
            .border(1.dp, g.line, shape)
            .drawBehind {
                // The inner highlight: a brighter line just inside the top edge, short of the corners.
                val inset = 18.dp.toPx()
                drawLine(g.highlight.copy(alpha = (g.highlight.alpha * 1.6f).coerceAtMost(1f)), Offset(inset, 1.5.dp.toPx()), Offset(size.width - inset, 1.5.dp.toPx()), 1.dp.toPx())
            }
            .padding(padding),
        horizontalAlignment = horizontalAlignment,
        verticalArrangement = verticalArrangement,
        content = content,
    )
}

/** A screen's heading: big, ExtraBold and tight, as the title page's show name. */
@Composable
fun ScreenTitle(text: String, modifier: Modifier = Modifier) {
    Text(text, modifier, color = glass.text, fontSize = 38.sp, lineHeight = 44.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.5).sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
}

/** The line under a screen's heading. */
@Composable
fun ScreenLead(text: String, modifier: Modifier = Modifier, color: Color = glass.muted) {
    Text(text, modifier, color = color, fontSize = 19.sp, lineHeight = 28.sp)
}

/** A dialog's heading (as "Still watching?" in the player). */
@Composable
fun DialogTitle(text: String, modifier: Modifier = Modifier, maxLines: Int = 2, textAlign: TextAlign? = null) {
    Text(text, modifier, textAlign = textAlign, color = glass.text, fontSize = 27.sp, lineHeight = 33.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.3).sp, maxLines = maxLines, overflow = TextOverflow.Ellipsis)
}

/** Body copy in a dialog or on a screen: 17 sp, readable from the sofa. */
@Composable
fun DialogText(text: String, modifier: Modifier = Modifier, color: Color = glass.muted, maxLines: Int = Int.MAX_VALUE, fontSize: TextUnit = 17.sp, textAlign: TextAlign? = null) {
    Text(text, modifier, textAlign = textAlign, color = color, fontSize = fontSize, lineHeight = fontSize * 1.45f, maxLines = maxLines, overflow = TextOverflow.Ellipsis)
}

/** A small caption above a group: "Room code", "Audio", "Server". */
@Composable
fun GroupLabel(text: String, modifier: Modifier = Modifier) {
    Text(text.uppercase(), modifier, color = glass.dim, fontSize = 13.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.2.sp, maxLines = 1)
}

/** "StreamHub" with "Hub" in the accent, as the mockup's top bar. */
@Composable
fun Wordmark(modifier: Modifier = Modifier, fontSize: TextUnit = 24.sp) {
    val t = LocalThemeTokens.current
    val g = glass
    Text(
        buildAnnotatedString {
            withStyle(SpanStyle(color = g.text)) { append("Stream") }
            withStyle(SpanStyle(color = t.accent)) { append("Hub") }
        },
        modifier, fontSize = fontSize, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.4).sp,
    )
}

/** A large code to read across the room (pairing, Watch together): wide-spaced, in the accent. */
@Composable
fun BigCode(text: String, modifier: Modifier = Modifier, fontSize: TextUnit = 64.sp) {
    Text(
        text, modifier, color = LocalThemeTokens.current.accent, fontSize = fontSize, lineHeight = fontSize * 1.1f,
        fontWeight = FontWeight.ExtraBold, letterSpacing = (fontSize.value / 10).sp, maxLines = 1,
        fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
    )
}

/** A QR code on a white tile with rounded corners, so it reads as part of the glass and still scans. */
@Composable
fun QrTile(text: String, modifier: Modifier = Modifier, size: Dp = 220.dp) {
    Box(modifier.shadow(16.dp, RoundedCornerShape(14.dp)).clip(RoundedCornerShape(14.dp)).background(Color.White).padding(10.dp)) {
        QrCode(text, size = size - 20.dp)
    }
}

/**
 * A row of a menu or a picker (the mockup's ⋯ menu): an optional icon, the label, an optional
 * detail at the end. Focus is the accent ring with a warm tint; [checked] puts a tick in front,
 * so the choice in force is never told by colour alone ([checkable] keeps the tick's column when
 * a row is not checked, so labels line up). Disabled rows are dimmed and skipped by the D-pad.
 */
@Composable
fun GlassRow(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
    detail: String? = null,
    checkable: Boolean = false,
    checked: Boolean = false,
    enabled: Boolean = true,
    height: Dp = 52.dp,
    trailing: (@Composable () -> Unit)? = null,
) {
    val t = LocalThemeTokens.current
    val g = glass
    FocusSurface(
        onClick = onClick,
        modifier = modifier.height(height),
        shape = RoundedCornerShape(11.dp),
        containerColor = if (checked) g.fillBottom else Color.Transparent,
        focusedContainerColor = g.rowFocus,
        scaleOnFocus = false,
        glow = false,
        enabled = enabled,
        ringColor = t.accent,
    ) { focused ->
        val alpha = if (enabled) 1f else 0.45f
        Row(Modifier.fillMaxSize().padding(horizontal = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            if (checkable || checked) {
                Box(Modifier.width(30.dp)) {
                    if (checked) Image(GlassIcons.Check, null, Modifier.size(20.dp), colorFilter = ColorFilter.tint(t.accent.copy(alpha = alpha)))
                }
            }
            if (icon != null) Image(icon, null, Modifier.padding(end = 14.dp).size(22.dp), colorFilter = ColorFilter.tint((if (focused) g.text else g.muted).copy(alpha = alpha)))
            Text(
                label, Modifier.weight(1f), color = g.text.copy(alpha = alpha), fontSize = 17.sp,
                fontWeight = if (focused || checked) FontWeight.SemiBold else FontWeight.Normal, maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            if (detail != null) Text(detail, color = (if (focused) g.text else g.muted).copy(alpha = alpha), fontSize = 15.sp, maxLines = 1, modifier = Modifier.padding(start = 12.dp))
            trailing?.invoke()
        }
    }
}

/** An On/Off pill at the end of a [GlassRow] (Autoplay): the word says it, the fill only repeats it. */
@Composable
fun OnOffPill(on: Boolean, modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    val g = glass
    Box(
        modifier.padding(start = 12.dp).height(28.dp).clip(RoundedCornerShape(14.dp))
            .background(if (on) Brush.verticalGradient(listOf(g.accentTop, g.accentBottom)) else Brush.verticalGradient(listOf(g.fillTop, g.fillBottom)))
            .border(1.dp, if (on) Color.Transparent else g.line, RoundedCornerShape(14.dp))
            .padding(horizontal = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(if (on) "On" else "Off", color = if (on) t.accentText else g.muted, fontSize = 14.sp, fontWeight = FontWeight.Bold)
    }
}

/** The motion spec of a focus lift, collapsed to a snap under reduced motion. */
@Composable
internal fun focusLift(focused: Boolean, to: Float): Float {
    val reduced = LocalReducedMotion.current
    val v by animateFloatAsState(if (focused) to else 1f, if (reduced) snap() else tween(Tokens.Focus.DURATION_MS), label = "lift")
    return v
}
