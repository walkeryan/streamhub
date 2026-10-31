package com.streamhub.tv.core.ui.focus

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.focus.FocusRequester

/**
 * Gives a screen its initial focus exactly once per screen instance (navigation back-stack entry).
 *
 * A plain `LaunchedEffect(Unit) { initial.requestFocus() }` runs again whenever NavHost recomposes
 * the destination after Back, which yanks focus to the default control instead of the tile that
 * opened the next screen (UAT P1-5). The "done" flag here is saveable, so it survives the
 * destination being disposed while another screen is on top. On return we request [restore]
 * instead: a container carrying `Modifier.focusRestorer()`, whose saved child (and the restorers
 * of the rows inside it) puts focus back where it was. With no [restore], nothing is requested on
 * return.
 *
 * [key] re-arms the initial focus when the screen's subject changes (e.g. Details for another id).
 */
@Composable
fun InitialFocusOnce(initial: FocusRequester, restore: FocusRequester? = null, key: String = "") {
    var doneFor by rememberSaveable { mutableStateOf<String?>(null) }
    LaunchedEffect(key) {
        withFrameNanos { } // let lazy items compose and attach before requesting
        if (doneFor != key) {
            runCatching { initial.requestFocus() }
            doneFor = key
        } else if (restore != null) {
            runCatching { restore.requestFocus() }
        }
    }
}
