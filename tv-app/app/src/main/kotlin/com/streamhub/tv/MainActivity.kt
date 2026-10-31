package com.streamhub.tv

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.streamhub.tv.core.data.repo.LocalPrefs
import com.streamhub.tv.core.ui.theme.StreamHubTheme
import com.streamhub.tv.core.ui.theme.ThemeRepository
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    @Inject lateinit var themes: ThemeRepository
    @Inject lateinit var prefs: LocalPrefs

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val selected by themes.selectedThemeId.collectAsStateWithLifecycle(ThemeRepository.DEFAULT_THEME)
            val preview by themes.preview.collectAsState()
            val adaptive by themes.adaptiveAccent.collectAsState()
            val reduced by prefs.reducedMotion.collectAsStateWithLifecycle(false)
            // Runtime theme switch: no activity restart; preview overrides selection while focused in Settings.
            StreamHubTheme(tokens = themes.tokens(preview ?: selected), adaptiveAccent = adaptive, reducedMotion = reduced) {
                StreamHubRoot()
            }
        }
    }
}
