package com.streamhub.tv

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.ShellEntry
import com.streamhub.tv.core.data.auth.shellEntry
import com.streamhub.tv.core.ui.theme.StreamHubTheme
import com.streamhub.tv.core.ui.theme.ThemeRepository
import com.streamhub.tv.feature.player.PlayerScreen
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * The native player, unchanged (rooms, recordings, live zapping, audio tracks, passthrough), as the
 * shell's playback surface. It finds what to play under [EXTRA_KEY], parked by PlaybackCoordinator
 * after the TV resolved the web app's request itself. Back leaves it and the web app is where it was.
 * The screen is kept awake by the video surface while the picture plays, and only then.
 */
@AndroidEntryPoint
class PlayerActivity : ComponentActivity() {
    @Inject lateinit var themes: ThemeRepository
    @Inject lateinit var sessions: SessionStore

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // PlayerViewModel reads the key from its SavedStateHandle, which an activity fills from its extras.
        if (intent.getStringExtra(EXTRA_KEY) == null) { finish(); return }
        setContent {
            val selected by themes.selectedThemeId.collectAsStateWithLifecycle(ThemeRepository.DEFAULT_THEME)
            StreamHubTheme(tokens = themes.tokens(selected)) { PlayerScreen(onExit = ::finish) }
        }
        // Signed out meanwhile (the web app had no token to give): nothing here can play.
        lifecycleScope.launch { sessions.session.first { shellEntry(it) == ShellEntry.Pair }; finish() }
    }

    companion object {
        /** Must be "key": the name PlayerViewModel reads. */
        const val EXTRA_KEY = "key"
    }
}
