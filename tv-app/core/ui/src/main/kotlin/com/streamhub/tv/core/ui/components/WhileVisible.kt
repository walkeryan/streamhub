package com.streamhub.tv.core.ui.components

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CoroutineScope

/**
 * Runs [block] while the screen it is composed in is in front (its lifecycle is resumed), and cancels
 * it when the screen is covered, left or the app goes to the background; it starts again on return.
 * For polling: nothing is asked of the server for a screen nobody is looking at. [key] restarts it.
 */
@Composable
fun WhileVisible(key: Any?, block: suspend CoroutineScope.() -> Unit) {
    val owner = LocalLifecycleOwner.current
    val current by rememberUpdatedState(block)
    LaunchedEffect(owner, key) {
        owner.repeatOnLifecycle(Lifecycle.State.RESUMED) { current() }
    }
}
