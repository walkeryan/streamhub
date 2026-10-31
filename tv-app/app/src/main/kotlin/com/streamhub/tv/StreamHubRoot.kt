package com.streamhub.tv

import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CalendarViewWeek
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.LiveTv
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.Tv
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.platform.LocalContext
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.handoff.HandoffResult
import com.streamhub.tv.core.data.repo.PlayEvent
import com.streamhub.tv.core.data.repo.PlaybackCoordinator
import com.streamhub.tv.core.data.repo.SessionEvents
import com.streamhub.tv.core.ui.components.HandoffQrDialog
import com.streamhub.tv.core.ui.components.LeftRail
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.PlayLauncher
import com.streamhub.tv.core.ui.components.RailItem
import com.streamhub.tv.core.ui.components.ScreenBackground
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.RailController
import com.streamhub.tv.core.ui.theme.Tokens
import com.streamhub.tv.feature.details.DetailsScreen
import com.streamhub.tv.feature.favorites.FavoritesScreen
import com.streamhub.tv.feature.guide.GuideScreen
import com.streamhub.tv.feature.home.BrowseScreen
import com.streamhub.tv.feature.home.HomeScreen
import com.streamhub.tv.feature.livetv.LiveTvScreen
import com.streamhub.tv.feature.onboarding.OnboardingScreen
import com.streamhub.tv.feature.player.PlayerScreen
import com.streamhub.tv.feature.search.SearchScreen
import com.streamhub.tv.feature.settings.SettingsScreen
import com.streamhub.tv.feature.sources.SourcesScreen
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import androidx.lifecycle.viewModelScope
import javax.inject.Inject

object Routes {
    const val ONBOARDING = "onboarding"
    const val HOME = "home"
    const val SEARCH = "search"
    const val LIVE = "live"
    const val GUIDE = "guide"
    const val MOVIES = "browse/movies"
    const val SHOWS = "browse/shows"
    const val FAVORITES = "favorites"
    const val SOURCES = "sources"
    const val SETTINGS = "settings"
    const val DETAILS = "details/{id}"
    const val PLAYER = "player/{key}"
    fun details(id: String) = "details/${android.net.Uri.encode(id)}"
    fun player(key: String) = "player/${android.net.Uri.encode(key)}"
}

private val RAIL = listOf(
    RailItem(Routes.HOME, "Home", Icons.Filled.Home),
    RailItem(Routes.SEARCH, "Search", Icons.Filled.Search),
    RailItem(Routes.LIVE, "Live TV", Icons.Filled.LiveTv),
    RailItem(Routes.GUIDE, "Guide", Icons.Filled.CalendarViewWeek),
    RailItem(Routes.MOVIES, "Movies", Icons.Filled.Movie),
    RailItem(Routes.SHOWS, "Shows", Icons.Filled.Tv),
    RailItem(Routes.FAVORITES, "Favorites", Icons.Filled.Favorite),
    RailItem(Routes.SOURCES, "Sources", Icons.Filled.Storage),
    RailItem(Routes.SETTINGS, "Settings", Icons.Filled.Settings),
)

@HiltViewModel
class RootViewModel @Inject constructor(
    sessionStore: SessionStore,
    val coordinator: PlaybackCoordinator,
    val sessionEvents: SessionEvents,
) : ViewModel() {
    /** null until DataStore has loaded (avoids flashing onboarding). */
    val session: StateFlow<Session?> = sessionStore.session.map<Session, Session?> { it }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)
}

@Composable
fun StreamHubRoot(vm: RootViewModel = hiltViewModel()) {
    val session by vm.session.collectAsStateWithLifecycle()
    val s = session ?: return ScreenBackground {}
    val nav = rememberNavController()
    val context = LocalContext.current
    var qr by remember { mutableStateOf<HandoffResult.ShowQr?>(null) }

    // Play events → player route (no intermediate screen) or hand-off.
    LaunchedEffect(Unit) {
        vm.coordinator.events.collect { e ->
            when (e) {
                is PlayEvent.StartPlayer -> nav.navigate(Routes.player(e.key)) { launchSingleTop = true }
                is PlayEvent.Handoff -> if (e.result is HandoffResult.ShowQr) qr = e.result as HandoffResult.ShowQr
                is PlayEvent.Error -> Toast.makeText(context, e.message, Toast.LENGTH_LONG).show()
            }
        }
    }
    LaunchedEffect(Unit) {
        vm.sessionEvents.expired.collect {
            nav.navigate(Routes.ONBOARDING) { popUpTo(0) { inclusive = true } }
        }
    }

    val launcher = remember(nav) {
        object : PlayLauncher {
            override fun play(id: String, sourceId: String?, catchupProgrammeId: String?) =
                vm.coordinator.play(id, sourceId, catchupProgrammeId)
            override fun openDetails(id: String) = nav.navigate(Routes.details(id))
        }
    }

    val start = if (s.isPaired) Routes.HOME else Routes.ONBOARDING
    CompositionLocalProvider(LocalPlayLauncher provides launcher) {
        ScreenBackground {
            AppShell(nav, start, profileName = s.profile?.name)
            qr?.let { HandoffQrDialog(it.webUrl, it.serviceName) { qr = null } }
        }
    }
}

@Composable
private fun AppShell(nav: NavHostController, start: String, profileName: String?) {
    val backStack by nav.currentBackStackEntryAsState()
    val route = backStack?.destination?.route
    val showRail = route != null && route != Routes.ONBOARDING && route != Routes.PLAYER
    var railExpanded by remember { mutableStateOf(false) }
    val railFocus = remember { FocusRequester() }
    val contentFocus = remember { FocusRequester() }
    val railController = remember { RailController { runCatching { railFocus.requestFocus() } } }
    val topLevel = RAIL.any { it.route == route }

    // Back on a top-level screen (other than Home) opens the rail first, then Home; never a dead end.
    BackHandler(enabled = showRail && topLevel && !railExpanded && route != Routes.HOME) { railController.openRail() }

    CompositionLocalProvider(LocalRailController provides railController) {
        Box(Modifier.fillMaxSize()) {
            // Content is offset by the collapsed rail width; the expanded rail overlays it (no layout shift).
            Box(
                Modifier
                    .fillMaxSize()
                    .padding(start = if (showRail) Tokens.Rail.CollapsedW else Tokens.Rail.CollapsedW * 0)
                    .focusRequester(contentFocus)
                    .focusRestorer(),
            ) {
                NavHost(nav, startDestination = start) {
                    composable(Routes.ONBOARDING) {
                        OnboardingScreen(onDone = { nav.navigate(Routes.HOME) { popUpTo(Routes.ONBOARDING) { inclusive = true } } })
                    }
                    composable(Routes.HOME) { HomeScreen() }
                    composable(Routes.SEARCH) { SearchScreen() }
                    composable(Routes.LIVE) { LiveTvScreen() }
                    composable(Routes.GUIDE) { GuideScreen() }
                    composable("browse/{section}", arguments = listOf(navArgument("section") { type = NavType.StringType })) { e ->
                        BrowseScreen(title = if (e.arguments?.getString("section") == "shows") "Shows" else "Movies")
                    }
                    composable(Routes.FAVORITES) { FavoritesScreen() }
                    composable(Routes.SOURCES) { SourcesScreen() }
                    composable(Routes.SETTINGS) { SettingsScreen(appVersion = BuildConfig.VERSION_NAME) }
                    composable(Routes.DETAILS, arguments = listOf(navArgument("id") { type = NavType.StringType })) { DetailsScreen() }
                    composable(Routes.PLAYER, arguments = listOf(navArgument("key") { type = NavType.StringType })) {
                        PlayerScreen(onExit = { nav.popBackStack() })
                    }
                }
            }
            if (showRail) {
                LeftRail(
                    items = RAIL,
                    selectedRoute = RAIL.firstOrNull { route?.startsWith(it.route) == true }?.route
                        ?: if (route?.startsWith("details") == true) null else route,
                    expanded = railExpanded,
                    onExpandedChange = { railExpanded = it },
                    onSelect = { item ->
                        nav.navigate(item.route) {
                            popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                            launchSingleTop = true
                            restoreState = true
                        }
                        runCatching { contentFocus.requestFocus() }
                    },
                    onExit = { runCatching { contentFocus.requestFocus() } },
                    profileName = profileName,
                    onProfile = { nav.navigate(Routes.SETTINGS) { launchSingleTop = true } },
                    firstItemRequester = railFocus,
                )
            }
        }
    }
}
