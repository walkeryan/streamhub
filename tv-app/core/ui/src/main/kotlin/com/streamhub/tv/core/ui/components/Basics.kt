package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Icon
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

/** Safe-area padding (48/27 dp). Backgrounds go outside this; interactive content inside. */
val SafeArea = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX, vertical = Tokens.Canvas.SafeMarginY)

@Composable
fun ScreenBackground(modifier: Modifier = Modifier, content: @Composable BoxScope.() -> Unit) {
    Box(modifier.fillMaxSize().background(LocalThemeTokens.current.bg), content = content)
}

enum class ButtonStyle { Primary, Secondary, Danger }

/** Pill button with token focus treatment. Primary = accent fill. */
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
    val (bg, fg) = when (style) {
        ButtonStyle.Primary -> t.accent to t.accentText
        ButtonStyle.Secondary -> t.surfaceAlt to t.text
        ButtonStyle.Danger -> t.danger to Color.White
    }
    FocusSurface(
        onClick = onClick,
        modifier = modifier.height(44.dp),
        shape = RoundedCornerShape(Tokens.Radius.Pill),
        containerColor = if (enabled) bg else bg.copy(alpha = 0.4f),
        focusedContainerColor = if (style == ButtonStyle.Secondary && enabled) t.text else if (enabled) bg else bg.copy(alpha = 0.4f),
        enabled = enabled,
        glow = style == ButtonStyle.Primary,
    ) { focused ->
        val color = when {
            !enabled -> fg.copy(alpha = 0.6f)
            style == ButtonStyle.Secondary && focused -> t.bg
            else -> fg
        }
        Row(
            Modifier.align(Alignment.Center).padding(horizontal = 20.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (icon != null) Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(20.dp))
            Text(text, color = color, style = MaterialTheme.typography.labelLarge, maxLines = 1)
        }
    }
}

/** Selectable chip (language chips, groups, filters). Selected = filled + check-mark semantics, not colour only. */
@Composable
fun ShChip(
    text: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    leading: ImageVector? = null,
) {
    val t = LocalThemeTokens.current
    FocusSurface(
        onClick = onClick,
        modifier = modifier.height(36.dp),
        shape = RoundedCornerShape(Tokens.Radius.Pill),
        containerColor = if (selected) t.accent else t.surfaceAlt,
        glow = false,
    ) {
        Row(Modifier.align(Alignment.Center).padding(horizontal = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            if (selected) Text("✓ ", color = t.accentText, style = MaterialTheme.typography.labelMedium)
            if (leading != null) Icon(leading, null, tint = if (selected) t.accentText else t.text, modifier = Modifier.size(16.dp).padding(end = 4.dp))
            Text(text, color = if (selected) t.accentText else t.text, style = MaterialTheme.typography.labelMedium, maxLines = 1)
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
    Text(
        text,
        modifier = modifier,
        style = MaterialTheme.typography.titleMedium,
        color = LocalThemeTokens.current.text,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
    )
}

@Composable
fun CenterMessage(title: String, body: String? = null, action: (@Composable () -> Unit)? = null) {
    val t = LocalThemeTokens.current
    Column(
        Modifier.fillMaxSize().padding(SafeArea),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(title, style = MaterialTheme.typography.headlineSmall, color = t.text)
        if (body != null) Text(body, style = MaterialTheme.typography.bodyLarge, color = t.textMuted, modifier = Modifier.padding(top = 8.dp).width(560.dp))
        if (action != null) Box(Modifier.padding(top = 24.dp)) { action() }
    }
}

@Composable
fun LoadingView() = CenterMessage("Loading…")
