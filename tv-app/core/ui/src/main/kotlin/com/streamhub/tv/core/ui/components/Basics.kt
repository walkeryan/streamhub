package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.streamhub.tv.core.ui.theme.glass
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Icon
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** Safe-area padding (48/27 dp). Backgrounds go outside this; interactive content inside. */
val SafeArea = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX, vertical = Tokens.Canvas.SafeMarginY)

/**
 * A native screen's backdrop: the theme's page colour (the mockup's #090d1a on Midnight) lit by two
 * soft glows, a warm one top-left in the accent and a cool one bottom-right, so the glass on top has
 * something to be glass against.
 */
@Composable
fun ScreenBackground(modifier: Modifier = Modifier, content: @Composable BoxScope.() -> Unit) {
    val t = LocalThemeTokens.current
    val g = glass
    val warm = t.accent.copy(alpha = if (t.isLight) 0.08f else 0.13f)
    val cool = (if (t.isLight) Color(0xFF6E86D8) else Color(0xFF3A4C9C)).copy(alpha = if (t.isLight) 0.10f else 0.22f)
    Box(
        modifier.fillMaxSize().background(g.backdrop).drawBehind {
            drawRect(Brush.radialGradient(listOf(warm, Color.Transparent), center = Offset(size.width * 0.12f, size.height * 0.05f), radius = size.width * 0.55f))
            drawRect(Brush.radialGradient(listOf(cool, Color.Transparent), center = Offset(size.width * 0.95f, size.height * 1.0f), radius = size.width * 0.6f))
        },
        content = content,
    )
}

enum class ButtonStyle { Primary, Secondary, Danger }

private val ButtonShape = RoundedCornerShape(10.dp)

/**
 * The text button of the glass look (the player's "Continue watching" / "Back to the show"):
 * Primary is the accent gradient, Secondary is glass, Danger is red. Focus turns any of them into a
 * solid light fill with the accent ring round it and lifts it slightly, so where focus is reads from
 * across the room on every style. Disabled buttons are dimmed and skipped by the D-pad.
 */
@Composable
fun ShButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
    style: ButtonStyle = ButtonStyle.Secondary,
    enabled: Boolean = true,
) {
    val t = LocalThemeTokens.current
    val g = glass
    val source = remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val scale = focusLift(focused, 1.06f)
    val fill = when {
        focused -> SolidColor(g.focusFill)
        style == ButtonStyle.Primary -> Brush.verticalGradient(listOf(g.accentTop, g.accentBottom))
        style == ButtonStyle.Danger -> SolidColor(t.danger)
        else -> Brush.verticalGradient(listOf(g.fillTop, g.fillBottom))
    }
    val ink = when {
        focused && style == ButtonStyle.Danger -> t.danger
        focused -> g.focusInk
        style == ButtonStyle.Primary -> t.accentText
        style == ButtonStyle.Danger -> Color.White
        else -> g.text
    }
    Box(
        modifier
            .graphicsLayer { scaleX = scale; scaleY = scale }
            .alpha(if (enabled) 1f else 0.45f)
            .height(46.dp)
            .then(if (focused) Modifier.shadow(14.dp, ButtonShape, ambientColor = t.accent, spotColor = t.accent) else Modifier)
            .clip(ButtonShape)
            .background(fill)
            .border(
                if (focused) 3.dp else 1.dp,
                when { focused -> t.accent; style == ButtonStyle.Secondary -> g.line; else -> Color.White.copy(alpha = 0.22f) },
                ButtonShape,
            )
            .clickable(interactionSource = source, indication = null, enabled = enabled, onClick = onClick)
            .padding(horizontal = 22.dp),
        contentAlignment = Alignment.Center,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            if (icon != null) Icon(icon, contentDescription = null, tint = ink, modifier = Modifier.size(20.dp))
            Text(text, color = ink, fontSize = 16.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/**
 * Selectable chip (sleep timer, filters). Selected = accent fill and a tick, not colour only; focus
 * is the same light fill and accent ring as [ShButton]. A disabled chip is shown dimmed and skipped
 * by focus: it states that a choice exists but cannot be made.
 */
@Composable
fun ShChip(
    text: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    leading: ImageVector? = null,
    enabled: Boolean = true,
) {
    val t = LocalThemeTokens.current
    val g = glass
    val source = remember { MutableInteractionSource() }
    val focused by source.collectIsFocusedAsState()
    val scale = focusLift(focused, 1.06f)
    val shape = RoundedCornerShape(Tokens.Radius.Pill)
    val fill = when {
        focused -> SolidColor(g.focusFill)
        selected -> Brush.verticalGradient(listOf(g.accentTop, g.accentBottom))
        else -> Brush.verticalGradient(listOf(g.fillTop, g.fillBottom))
    }
    val ink = when { focused -> g.focusInk; selected -> t.accentText; else -> g.text }
    Box(
        modifier
            .graphicsLayer { scaleX = scale; scaleY = scale }
            .alpha(if (enabled) 1f else 0.45f)
            .height(40.dp)
            .clip(shape)
            .background(fill)
            .border(if (focused) 3.dp else 1.dp, if (focused) t.accent else if (selected) Color.Transparent else g.line, shape)
            .clickable(interactionSource = source, indication = null, enabled = enabled, onClick = onClick)
            .padding(horizontal = 16.dp),
        contentAlignment = Alignment.Center,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            if (selected) Icon(GlassIcons.Check, null, tint = ink, modifier = Modifier.size(16.dp))
            if (leading != null) Icon(leading, null, tint = ink, modifier = Modifier.size(16.dp))
            Text(text, color = ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
        }
    }
}

@Composable
fun ProgressLine(fraction: Float, modifier: Modifier = Modifier, height: androidx.compose.ui.unit.Dp = 4.dp) {
    val t = LocalThemeTokens.current
    Box(modifier.fillMaxWidth().height(height).background(t.text.copy(alpha = 0.25f), RoundedCornerShape(2.dp))) {
        Box(Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).height(height).background(t.accent, RoundedCornerShape(2.dp)))
    }
}

@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier) {
    Text(text, modifier = modifier, color = glass.text, fontSize = 21.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
}

@Composable
fun CenterMessage(title: String, body: String? = null, action: (@Composable () -> Unit)? = null) {
    Column(
        Modifier.fillMaxSize().padding(SafeArea),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        DialogTitle(title)
        if (body != null) ScreenLead(body, Modifier.padding(top = 10.dp).width(600.dp))
        if (action != null) Box(Modifier.padding(top = 28.dp)) { action() }
    }
}

@Composable
fun LoadingView() = CenterMessage("Loading…")
