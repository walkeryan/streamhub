package com.streamhub.tv.core.ui.theme

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.themeStore by preferencesDataStore("theme")

/**
 * Runtime theme source. The selected theme id is persisted per device (and pushed to the
 * profile's `theme_id` on the server by Settings). A *preview* override lets the theme picker
 * show a theme live while focused, without committing it.
 */
@Singleton
class ThemeRepository @Inject constructor(@ApplicationContext private val context: Context) {
    private val key = stringPreferencesKey("theme_id")

    val selectedThemeId: Flow<String> = context.themeStore.data.map { it[key] ?: DEFAULT_THEME }

    private val _preview = MutableStateFlow<String?>(null)
    val preview: StateFlow<String?> = _preview.asStateFlow()

    /** Dominant colour of the focused poster for the Poster Adaptive theme. */
    private val _adaptiveAccent = MutableStateFlow<androidx.compose.ui.graphics.Color?>(null)
    val adaptiveAccent: StateFlow<androidx.compose.ui.graphics.Color?> = _adaptiveAccent.asStateFlow()

    suspend fun select(id: String) {
        context.themeStore.edit { it[key] = if (id in Tokens.themes) id else DEFAULT_THEME }
        _preview.value = null
    }

    fun preview(id: String?) { _preview.value = id }

    fun setAdaptiveAccent(c: androidx.compose.ui.graphics.Color?) { _adaptiveAccent.value = c }

    fun tokens(id: String): ThemeTokens = Tokens.themes[id] ?: Tokens.themes.getValue(DEFAULT_THEME)

    companion object {
        const val DEFAULT_THEME = "midnight"
    }
}
