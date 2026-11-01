package com.streamhub.tv

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.streamhub.tv.core.data.model.LiveGuideFilters
import com.streamhub.tv.core.ui.components.LoadingView
import com.streamhub.tv.core.ui.components.ShChip
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.Tokens
import com.streamhub.tv.feature.guide.GuideScreen
import com.streamhub.tv.feature.livetv.LiveTvScreen
import com.streamhub.tv.feature.livetv.LiveTvViewModel

/** One remote-friendly destination. Only the visible content can own a preview player. */
@Composable
fun LiveDestination(startInGuide: Boolean = false, vm: LiveTvViewModel = hiltViewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    var guide by rememberSaveable(startInGuide) { mutableStateOf(startInGuide) }
    val rail = LocalRailController.current
    val channelsFocus = remember { FocusRequester() }
    var returnToChannels by remember { mutableStateOf(false) }
    LaunchedEffect(guide, returnToChannels) {
        if (!guide && returnToChannels) {
            withFrameNanos { }
            channelsFocus.requestFocus()
            returnToChannels = false
        }
    }
    val filters = LiveGuideFilters(
        country = state.country,
        profileScope = state.profileScope,
        languages = state.selectedLanguages?.sorted().orEmpty(),
        group = state.selectedGroup,
        favorites = state.favoritesOnly,
    )
    DisposableEffect(guide) {
        if (guide) vm.stopPreview()
        onDispose { vm.stopPreview() }
    }
    // Back leaves Guide for Channels; the next Back follows the existing rail navigation.
    BackHandler(enabled = guide) { guide = false; returnToChannels = true }
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = 8.dp).focusRestorer(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ShChip("Guide", guide, { guide = true }, Modifier.leftOpensRail(rail))
            ShChip("Channels", !guide, { guide = false }, Modifier.focusRequester(channelsFocus))
            ShChip("US region", state.country == "US", { vm.selectCountry("US") })
            ShChip("All regions", state.country == "all", { vm.selectCountry("all") })
            ShChip("Unknown region", state.country == "und", { vm.selectCountry("und") })
        }
        if (!state.ready) {
            LoadingView()
        } else if (guide) {
            LiveTvScreen(vm = vm, filtersOnly = true)
            Box(Modifier.weight(1f)) { GuideScreen(filters = filters, compact = true) }
        } else {
            Box(Modifier.weight(1f)) { LiveTvScreen(vm = vm) }
        }
    }
}
