package com.streamhub.tv.feature.home

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.ui.components.ButtonStyle
import com.streamhub.tv.core.ui.components.CenterMessage
import com.streamhub.tv.core.ui.components.HeroBackdrop
import com.streamhub.tv.core.ui.components.HeroIcons
import com.streamhub.tv.core.ui.components.HeroInfo
import com.streamhub.tv.core.ui.components.LoadingView
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.MediaRow
import com.streamhub.tv.core.ui.components.ShButton
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.PivotScroll
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.Tokens
import kotlinx.coroutines.delay

@Composable
fun HomeScreen(vm: HomeViewModel = hiltViewModel()) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    when (val u = ui) {
        HomeUi.Loading -> LoadingView()
        is HomeUi.Error -> CenterMessage("Couldn't load Home", u.message) { ShButton("Retry", vm::refresh, style = ButtonStyle.Primary) }
        is HomeUi.Ready -> if (u.rows.isEmpty() && u.hero.isEmpty()) {
            CenterMessage("Nothing here yet", "Add a source in Sources to fill your Home screen.")
        } else {
            HomeContent(u)
        }
    }
}

@Composable
private fun HomeContent(u: HomeUi.Ready) {
    val launcher = LocalPlayLauncher.current
    val rail = LocalRailController.current
    var heroIndex by rememberSaveable { mutableStateOf(0) }
    var focusedTile by remember { mutableStateOf<Tile?>(null) }
    var heroButtonsFocused by remember { mutableStateOf(false) }
    val heroTile = focusedTile ?: u.hero.getOrNull(heroIndex % u.hero.size.coerceAtLeast(1))
    val playFocus = remember { FocusRequester() }

    // Auto-advance every 8 s while the hero is showing its own items, paused while a hero button is focused.
    LaunchedEffect(focusedTile, heroButtonsFocused, u.hero.size) {
        if (focusedTile == null && !heroButtonsFocused && u.hero.size > 1) {
            while (true) { delay(8_000); heroIndex = (heroIndex + 1) % u.hero.size }
        }
    }
    LaunchedEffect(Unit) { runCatching { playFocus.requestFocus() } }

    val listState = rememberLazyListState()
    Box(Modifier.fillMaxSize()) {
        HeroBackdrop(heroTile)
        PivotScroll(leadingOffset = 0.dp) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize().focusRestorer(),
                contentPadding = PaddingValues(bottom = Tokens.Canvas.SafeMarginY),
            ) {
                item(key = "hero") {
                    HeroInfo(
                        tile = heroTile,
                        modifier = Modifier.padding(start = Tokens.Canvas.SafeMarginX, top = Tokens.Canvas.SafeMarginY + 40.dp, bottom = 16.dp),
                        height = 260.dp,
                    ) {
                        val tile = heroTile
                        val inApp = tile?.playableInApp == true
                        ShButton(
                            if (inApp) "Play" else "Open in app",
                            onClick = { tile?.let { launcher.play(it.id) } },
                            icon = if (inApp) HeroIcons.Play else HeroIcons.Open,
                            style = ButtonStyle.Primary,
                            modifier = Modifier.focusRequester(playFocus).leftOpensRail(rail)
                                .onFocusChanged { heroButtonsFocused = it.isFocused; if (it.isFocused) focusedTile = null },
                        )
                        ShButton(
                            "Details",
                            onClick = { tile?.let { launcher.openDetails(it.id) } },
                            icon = HeroIcons.Info,
                            modifier = Modifier.onFocusChanged { if (it.isFocused) heroButtonsFocused = true },
                        )
                    }
                }
                items(u.rows, key = { it.id }) { row ->
                    MediaRow(
                        row = row,
                        onPlay = { launcher.play(it.id) },
                        onOpen = { launcher.openDetails(it.id) },
                        onFocusTile = { focusedTile = it },
                        state = rememberLazyListState(),
                    )
                }
                item { Spacer(Modifier.fillMaxWidth().height(120.dp)) }
            }
        }
    }
}
