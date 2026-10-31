package com.streamhub.tv.core.ui.focus

import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type

/** Lets content open the left rail (BRIEF: "Left from the first card in any row opens the rail"). */
fun interface RailController {
    fun openRail()
}

val LocalRailController = staticCompositionLocalOf { RailController { } }

/** Attach to the first item of a row/list: D-pad Left opens the rail instead of being swallowed. */
fun Modifier.leftOpensRail(controller: RailController, enabled: Boolean = true): Modifier =
    if (!enabled) this else onPreviewKeyEvent { e ->
        if (e.type == KeyEventType.KeyDown && e.key == Key.DirectionLeft) {
            controller.openRail(); true
        } else {
            false
        }
    }
