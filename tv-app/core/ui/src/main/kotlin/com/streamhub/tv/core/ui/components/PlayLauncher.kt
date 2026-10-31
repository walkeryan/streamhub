package com.streamhub.tv.core.ui.components

import androidx.compose.runtime.staticCompositionLocalOf

/**
 * App-wide "press Play": implementations POST /playback/resolve then open the player immediately
 * (or hand off). Provided by :app from PlaybackCoordinator so features need no player dependency.
 */
interface PlayLauncher {
    fun play(id: String, sourceId: String? = null, catchupProgrammeId: String? = null)
    fun openDetails(id: String)
}

val LocalPlayLauncher = staticCompositionLocalOf<PlayLauncher> {
    object : PlayLauncher {
        override fun play(id: String, sourceId: String?, catchupProgrammeId: String?) = Unit
        override fun openDetails(id: String) = Unit
    }
}
