package com.streamhub.tv.core.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Icon
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.focus.FocusSurface
import com.streamhub.tv.core.ui.theme.LocalReducedMotion
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens

data class RailItem(val route: String, val label: String, val icon: ImageVector)

/**
 * Rail geometry. Items are inset to the TV safe area (BRIEF UAT #3: nothing interactive outside
 * 48/27 dp); only the rail background bleeds to the screen edge. tokens.json still says 72/240 dp,
 * which predates the safe-area inset (72 dp minus a 48 dp margin leaves no room for a 44 dp item),
 * so the shell uses these widths instead of Tokens.Rail.
 */
object RailMetrics {
    /** Width of a rail item (icon button) when collapsed. */
    val ItemW = 44.dp
    /** Gap between the items and the content edge. */
    val InnerGutter = 12.dp
    /** SafeMarginX (48) + item (44) + gutter (12) = 104 dp. Content is offset by this. */
    val CollapsedW = Tokens.Canvas.SafeMarginX + ItemW + InnerGutter
    /** Expanded width keeps the 240 dp token's label room (240 − 72 + 104). */
    val ExpandedW = Tokens.Rail.ExpandedW + (CollapsedW - Tokens.Rail.CollapsedW)
}

/**
 * Collapsible left rail. Icons only when collapsed ([RailMetrics.CollapsedW]), icon+label when expanded.
 * Expands while any item has focus. Right/Back hands focus back to content via [onExit].
 * Rendered as an overlay over content (content is laid out with collapsed width offset), so
 * expanding never shifts layout.
 */
@Composable
fun LeftRail(
    items: List<RailItem>,
    selectedRoute: String?,
    expanded: Boolean,
    onExpandedChange: (Boolean) -> Unit,
    onSelect: (RailItem) -> Unit,
    onExit: () -> Unit,
    profileName: String?,
    onProfile: () -> Unit,
    firstItemRequester: FocusRequester,
    modifier: Modifier = Modifier,
) {
    val t = LocalThemeTokens.current
    val dur = if (LocalReducedMotion.current) 0 else Tokens.Motion.BASE_MS
    val width by animateDpAsState(if (expanded) RailMetrics.ExpandedW else RailMetrics.CollapsedW, tween(dur), label = "rail")
    val selectedIndex = items.indexOfFirst { it.route == selectedRoute }.coerceAtLeast(0)
    val requesters = remember(items.size) { List(items.size) { FocusRequester() } }

    Column(
        modifier
            .fillMaxHeight()
            .width(width)
            .background(if (expanded) t.surface else t.bg.copy(alpha = 0.85f))
            // Background above bleeds to the edges; focusable items start inside the 48/27 dp safe area.
            .padding(
                start = Tokens.Canvas.SafeMarginX,
                end = RailMetrics.InnerGutter,
                top = Tokens.Canvas.SafeMarginY,
                bottom = Tokens.Canvas.SafeMarginY,
            )
            .onFocusChanged { onExpandedChange(it.hasFocus) }
            .focusRestorer(requesters.getOrElse(selectedIndex) { FocusRequester.Default })
            .focusGroup()
            .onPreviewKeyEvent { e ->
                when {
                    e.key == Key.Back -> {
                        // Consume both edges so the KeyUp can't reach a screen BackHandler and re-open us.
                        if (e.type == KeyEventType.KeyDown) onExit()
                        true
                    }
                    e.type == KeyEventType.KeyDown && e.key == Key.DirectionRight -> { onExit(); true }
                    else -> false
                }
            },
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        // Keep every destination reachable at TV logical heights as the menu grows.
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()), verticalArrangement=Arrangement.spacedBy(6.dp)) {
        items.forEachIndexed { i, item ->
            val req = if (i == selectedIndex) firstItemRequester else requesters[i]
            RailButton(
                item = item,
                selected = item.route == selectedRoute,
                expanded = expanded,
                modifier = Modifier.focusRequester(req).then(if (i == selectedIndex) Modifier.focusRequester(requesters[i]) else Modifier),
                onClick = { onSelect(item) },
            )
        }
        }
        FocusSurface(onClick = onProfile, modifier = Modifier.fillMaxWidth().height(48.dp), containerColor = androidx.compose.ui.graphics.Color.Transparent, focusedContainerColor = t.surfaceAlt, scaleOnFocus = false, glow = false) {
            Row(Modifier.align(Alignment.CenterStart).padding(horizontal = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(36.dp).background(t.accent, CircleShape), contentAlignment = Alignment.Center) {
                    Text(profileName?.take(1)?.uppercase() ?: "?", color = t.accentText, style = MaterialTheme.typography.titleSmall)
                }
                AnimatedVisibility(expanded, enter = fadeIn(tween(dur)), exit = fadeOut(tween(dur))) {
                    Text(profileName ?: "Profile", color = t.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = 12.dp), maxLines = 1)
                }
            }
        }
    }
}

@Composable
private fun RailButton(item: RailItem, selected: Boolean, expanded: Boolean, modifier: Modifier, onClick: () -> Unit) {
    val t = LocalThemeTokens.current
    FocusSurface(
        onClick = onClick,
        modifier = modifier.fillMaxWidth().height(44.dp),
        shape = RoundedCornerShape(Tokens.Radius.Md),
        containerColor = if (selected) t.surfaceAlt else androidx.compose.ui.graphics.Color.Transparent,
        focusedContainerColor = t.text,
        scaleOnFocus = false,
        glow = false,
    ) { focused ->
        val fg = if (focused) t.bg else if (selected) t.accent else t.text
        Row(Modifier.align(Alignment.CenterStart).padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(item.icon, contentDescription = item.label, tint = fg, modifier = Modifier.size(24.dp))
            if (expanded) {
                Text(item.label, color = fg, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(start = 16.dp), maxLines = 1)
            }
        }
        // Selected indicator bar: shape cue, not colour only.
        if (selected && !focused) {
            Box(Modifier.align(Alignment.CenterStart).width(3.dp).height(20.dp).background(t.accent, RoundedCornerShape(2.dp)))
        }
    }
}
