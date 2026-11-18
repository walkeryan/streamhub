package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.streamhub.tv.core.ui.theme.glass
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.ui.theme.LocalThemeTokens

/**
 * D-pad friendly text field in glass: the accent ring and a brighter fill when focused; OK opens the
 * system IME (voice via IME mic).
 */
@Composable
fun ShTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    placeholder: String = "",
    password: Boolean = false,
    keyboardType: KeyboardType = KeyboardType.Text,
    imeAction: ImeAction = ImeAction.Next,
    onImeAction: () -> Unit = {},
) {
    val t = LocalThemeTokens.current
    val g = glass
    val interaction = remember { MutableInteractionSource() }
    val interactionFocused by interaction.collectIsFocusedAsState()
    // The field's own focus state as well: not every text field version reports focus through the interaction source.
    var hasFocus by remember { mutableStateOf(false) }
    val focused = interactionFocused || hasFocus
    val shape = RoundedCornerShape(12.dp)
    Column(modifier) {
        GroupLabel(label, Modifier.padding(bottom = 8.dp))
        Box(
            Modifier
                .fillMaxWidth()
                .height(56.dp)
                .then(if (focused) Modifier.shadow(14.dp, shape, ambientColor = t.accent, spotColor = t.accent) else Modifier)
                .clip(shape)
                .background(if (focused) Brush.verticalGradient(listOf(g.fillTop.copy(alpha = g.fillTop.alpha * 1.6f), g.fillBottom.copy(alpha = g.fillBottom.alpha * 2f))) else Brush.verticalGradient(listOf(g.fillTop, g.fillBottom)))
                .border(if (focused) 3.dp else 1.dp, if (focused) t.accent else g.line, shape)
                .padding(horizontal = 18.dp),
            contentAlignment = Alignment.CenterStart,
        ) {
            if (value.isEmpty() && placeholder.isNotEmpty()) {
                Text(placeholder, color = g.dim, fontSize = 19.sp)
            }
            BasicTextField(
                value = value,
                onValueChange = onValueChange,
                singleLine = true,
                interactionSource = interaction,
                textStyle = MaterialTheme.typography.bodyLarge.copy(color = g.text, fontSize = 19.sp, fontWeight = FontWeight.Medium),
                cursorBrush = SolidColor(t.accent),
                visualTransformation = if (password) PasswordVisualTransformation() else VisualTransformation.None,
                keyboardOptions = KeyboardOptions(keyboardType = keyboardType, imeAction = imeAction),
                keyboardActions = KeyboardActions(onAny = { onImeAction() }),
                modifier = Modifier.fillMaxWidth().onFocusChanged { hasFocus = it.isFocused },
            )
        }
    }
}
