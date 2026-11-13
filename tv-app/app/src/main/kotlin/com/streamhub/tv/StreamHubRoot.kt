package com.streamhub.tv

import android.app.Activity
import android.os.SystemClock
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.LiveTv
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SportsSoccer
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.Tv
import androidx.compose.material.icons.filled.VideoLibrary
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.handoff.HandoffResult
import com.streamhub.tv.core.data.model.Permissions
import com.streamhub.tv.core.data.repo.LocalPrefs
import com.streamhub.tv.core.data.repo.MeRepository
import com.streamhub.tv.core.data.repo.MeState
import com.streamhub.tv.core.data.repo.PlayEvent
import com.streamhub.tv.core.data.repo.RecordingAvailability
import com.streamhub.tv.core.data.repo.RecordingsRepository
import com.streamhub.tv.core.data.repo.PlaybackCoordinator
import com.streamhub.tv.core.data.repo.SessionEvents
import com.streamhub.tv.core.ui.components.ErrorDialog
import com.streamhub.tv.core.ui.components.HandoffQrDialog
import com.streamhub.tv.core.ui.components.isDisclaimerAccepted
import com.streamhub.tv.core.ui.components.LeftRail
import com.streamhub.tv.core.ui.components.LocalPlayLauncher
import com.streamhub.tv.core.ui.components.PlayLauncher
import com.streamhub.tv.core.ui.components.RailItem
import com.streamhub.tv.core.ui.components.RailMetrics
import com.streamhub.tv.core.ui.components.ScreenBackground
import com.streamhub.tv.core.ui.components.AudioSourceDialog
import com.streamhub.tv.core.ui.focus.BackAction
import com.streamhub.tv.core.ui.focus.BackPolicy
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.RailController
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import com.streamhub.tv.feature.details.DetailsScreen
import com.streamhub.tv.feature.favorites.FavoritesScreen
import com.streamhub.tv.feature.home.DiscoveryScreen
import com.streamhub.tv.feature.player.MultiviewScreen
import com.streamhub.tv.feature.home.BrowseScreen
import com.streamhub.tv.feature.home.HomeScreen
import com.streamhub.tv.feature.home.SportsScreen
import com.streamhub.tv.feature.onboarding.OnboardingScreen
import com.streamhub.tv.feature.onboarding.WhoIsWatchingScreen
import com.streamhub.tv.feature.player.PlayerScreen
import com.streamhub.tv.feature.recordings.RecordingsScreen
import com.streamhub.tv.feature.requests.RequestsScreen
import com.streamhub.tv.feature.search.JoinRoomScreen
import com.streamhub.tv.feature.search.SearchScreen
import com.streamhub.tv.feature.settings.SettingsScreen
import com.streamhub.tv.feature.sources.SourcesScreen
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.stateIn
import androidx.lifecycle.viewModelScope
import javax.inject.Inject

object Routes {
    const val ONBOARDING = "onboarding"
    const val HOME = BackPolicy.HOME_ROUTE
    const val SEARCH = "search"
    const val LIVE = "live"
    const val GUIDE = "guide"
    const val SPORTS = "sports"
    const val DISCOVER = "discover/{section}"
    const val MULTIVIEW = "multiview"
    fun discover(section:String="movies") = "discover/$section"
    const val BROWSE = "browse/{section}"
    /** The grid for one category, opened by a shelf's "See all". Not a top-level screen: Back returns to the shelves. */
    const val BROWSE_ALL = "browse/{section}/all?category={category}"
    fun browseAll(section: String, category: String) = "browse/${android.net.Uri.encode(section)}/all?category=${android.net.Uri.encode(category)}"
    const val MOVIES = "browse/movies"
    const val SHOWS = "browse/shows"
    const val FAVORITES = "favorites"
    const val SOURCES = "sources"
    const val SETTINGS = "settings"
    const val RECORDINGS = "recordings"
    /** My requests / all requests; opened from Settings, so Back returns there. */
    const val REQUESTS = "requests"
    /** Join a watch room by its code. */
    const val JOIN_ROOM = "rooms/join"
    /** "Who's watching?" opened from the rail or Settings: Back returns. */
    const val WHO = "who"
    /** The same picker standing between a cold start (or pairing) and the app on a shared TV: Back offers to exit. */
    const val WHO_GATE = "who/gate"
    const val DETAILS = "details/{id}"
    const val PLAYER = "player/{key}"
    fun details(id: String) = "details/${android.net.Uri.encode(id)}"
    fun player(key: String) = "player/${android.net.Uri.encode(key)}"
}

/** Screens that own the whole display: no rail beside them. */
private val FULL_SCREEN_ROUTES = setOf(Routes.ONBOARDING, Routes.PLAYER, Routes.MULTIVIEW, Routes.WHO, Routes.WHO_GATE)

/**
 * The rail for what the signed-in user may do. The rail has no "more" area, so a destination that
 * depends on a permission sits beside the one it belongs with and is simply absent without it.
 */
private fun railItems(access: MeState, recordings: RecordingAvailability): List<RailItem> = listOfNotNull(
    RailItem(Routes.HOME, "Home", Icons.Filled.Home),
    RailItem(Routes.SEARCH, "Search", Icons.Filled.Search),
    RailItem(Routes.JOIN_ROOM, "Join a room", Icons.Filled.Group).takeIf { access.can(Permissions.ROOMS_JOIN) },
    RailItem(Routes.LIVE, "Live TV", Icons.Filled.LiveTv),
    // Only where there is something behind it: permission to see recordings and a server that records.
    RailItem(Routes.RECORDINGS, "Recordings", Icons.Filled.VideoLibrary).takeIf { access.can(Permissions.RECORDINGS_VIEW) && recordings.enabled },
    RailItem(Routes.SPORTS, "Sports", Icons.Filled.SportsSoccer),
    RailItem(Routes.discover(), "Discover", Icons.Filled.Search),
    RailItem(Routes.MOVIES, "Movies", Icons.Filled.Movie),
    RailItem(Routes.SHOWS, "Shows", Icons.Filled.Tv),
    RailItem(Routes.FAVORITES, "Favorites", Icons.Filled.Favorite),
    RailItem(Routes.SOURCES, "Sources", Icons.Filled.Storage),
    RailItem(Routes.SETTINGS, "Settings", Icons.Filled.Settings),
)

@HiltViewModel
class RootViewModel @Inject constructor(
    sessionStore: SessionStore,
    prefs: LocalPrefs,
    val coordinator: PlaybackCoordinator,
    val sessionEvents: SessionEvents,
    val me: MeRepository,
    val recordings: RecordingsRepository,
) : ViewModel() {
    /** null until DataStore has loaded (avoids flashing onboarding). */
    val session: StateFlow<Session?> = sessionStore.session.map<Session, Session?> { it }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** null until loaded. A paired TV that never accepted the legal notice still goes through the gate. */
    val disclaimerAccepted: StateFlow<Boolean?> = prefs.disclaimerAcceptedVersion.map<String?, Boolean?> { isDisclaimerAccepted(it) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /**
     * Whether this was a shared TV the last time the server said; null until DataStore has loaded.
     * It picks the first screen, so a household TV opens on "Who's watching?" without first showing
     * the last person's Home.
     */
    val sharedHint: StateFlow<SharedHint?> = prefs.sharedDevice.map<Boolean?, SharedHint?> {
        when (it) { true -> SharedHint.Shared; false -> SharedHint.Personal; null -> SharedHint.Unknown }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** The cold-start picker is wanted after all: the server says this is a shared TV and the hint did not. */
    private val _gateRequests = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val gateRequests: SharedFlow<Unit> = _gateRequests
    private var gateSettled = false

    /** The cold-start question "who is watching?" has been put, or does not arise: it is not put again in this run. */
    fun settleGate() { gateSettled = true }

    init {
        // Whether there is a recorder behind the Recordings destination and the Record actions: asked
        // again whenever the answer to "what may this user do" changes (start, pairing, a switch).
        viewModelScope.launch { me.state.collectLatest { recordings.refreshAvailability() } }
        // What the signed-in user may do: read on start, forgotten when the TV is signed out.
        viewModelScope.launch {
            sessionStore.session.map { it.isPaired }.distinctUntilChanged().collect { paired ->
                if (!paired) { me.clear(); return@collect }
                val access = if (me.state.value == MeState.Unknown) me.refresh() else me.state.value
                if (access != MeState.Unknown && !gateSettled) {
                    gateSettled = true
                    if (access.sharedDevice) _gateRequests.tryEmit(Unit)
                }
            }
        }
    }
}

enum class SharedHint { Unknown, Shared, Personal }

@Composable
fun StreamHubRoot(vm: RootViewModel = hiltViewModel()) {
    val session by vm.session.collectAsStateWithLifecycle()
    val accepted by vm.disclaimerAccepted.collectAsStateWithLifecycle()
    val s = session ?: return ScreenBackground {}
    val localAccepted = accepted ?: return ScreenBackground {}
    val sharedHint = vm.sharedHint.collectAsStateWithLifecycle().value ?: return ScreenBackground {}
    val access by vm.me.state.collectAsStateWithLifecycle()
    val recordings by vm.recordings.availability.collectAsStateWithLifecycle()
    val nav = rememberNavController()
    var audioChoice by remember { mutableStateOf<PlayEvent.ChooseAudio?>(null) }
    var playError by remember { mutableStateOf<UserError?>(null) }
    var qr by remember { mutableStateOf<HandoffResult.ShowQr?>(null) }

    LaunchedEffect(s.serverUrl, s.profile?.id, s.profile?.languages) { audioChoice = null; playError = null; vm.coordinator.cancelPending(); vm.coordinator.dismissError() }
    var previousProfile by remember { mutableStateOf(s.profile?.id) }
    var previousServer by remember { mutableStateOf(s.serverUrl) }
    LaunchedEffect(s.serverUrl,s.profile?.id) {
        val oldProfile=previousProfile
        val oldServer=previousServer
        previousProfile=s.profile?.id; previousServer=s.serverUrl
        if(oldProfile!=null && s.profile?.id!=null && (oldProfile!=s.profile?.id || oldServer!=s.serverUrl)) {
            // Remove old Details/Search/Settings back-stack state; persisted per-profile data stays intact.
            nav.navigate(if(oldServer==s.serverUrl) Routes.HOME else Routes.ONBOARDING) { popUpTo(nav.graph.id) {inclusive=true}; launchSingleTop=true }
        }
    }


    // Play events → player route (no intermediate screen) or hand-off.
    LaunchedEffect(Unit) {
        vm.coordinator.events.collect { e ->
            when (e) {
                is PlayEvent.ChooseAudio -> { playError = null; audioChoice = e }
                is PlayEvent.StartPlayer -> { playError = null; nav.navigate(Routes.player(e.key)) { launchSingleTop = true } }
                is PlayEvent.Handoff -> if (e.result is HandoffResult.ShowQr) qr = e.result as HandoffResult.ShowQr
                is PlayEvent.Error -> { audioChoice = null; playError = e.error }
            }
        }
    }
    LaunchedEffect(Unit) {
        vm.sessionEvents.expired.collect {
            audioChoice = null
            playError = null
            vm.coordinator.cancelPending()
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

    // Decided once: later session/acceptance changes navigate explicitly (onboarding onDone, expiry) rather
    // than swapping NavHost's start destination under a live back stack.
    val start = remember {
        val ok = localAccepted || isDisclaimerAccepted(s.profile?.disclaimerAcceptedVersion)
        when {
            !(s.isPaired && ok) -> Routes.ONBOARDING
            // A shared TV asks who is watching before anything else, on every cold start.
            sharedHint == SharedHint.Shared -> { vm.settleGate(); Routes.WHO_GATE }
            else -> Routes.HOME
        }
    }
    // The hint was missing or out of date and the server says this is a shared TV: ask now. Not while
    // the legal notice or pairing is on screen; onboarding asks the same question when it finishes.
    LaunchedEffect(Unit) {
        vm.gateRequests.collect {
            if (nav.currentDestination?.route != Routes.ONBOARDING) {
                nav.navigate(Routes.WHO_GATE) { popUpTo(nav.graph.id) { inclusive = true }; launchSingleTop = true }
            }
        }
    }
    CompositionLocalProvider(LocalPlayLauncher provides launcher) {
        ScreenBackground {
            AppShell(nav, start, profileName = s.profile?.name, access = access, recordings = recordings, sharedDevice = { vm.me.state.value.sharedDevice }, onGateSettled = vm::settleGate)
            audioChoice?.let { choice ->
                AudioSourceDialog(choice.options, onDismiss = { audioChoice = null; vm.coordinator.cancelPending() }) { variant, confirmed ->
                    audioChoice = null
                    vm.coordinator.chooseAudio(choice.request, variant, confirmed)
                }
            }
            qr?.let { HandoffQrDialog(it.webUrl, it.serviceName) { qr = null } }
            // Play failures: a dialog the remote can answer, focused on Try again when retrying can help.
            playError?.let { error ->
                ErrorDialog(
                    error,
                    onRetry = { playError = null; vm.coordinator.retryLast() },
                    onDismiss = { playError = null; vm.coordinator.dismissError() },
                )
            }
        }
    }
}

@Composable
private fun AppShell(
    nav: NavHostController,
    start: String,
    profileName: String?,
    access: MeState,
    recordings: RecordingAvailability,
    /** Asked at the moment of a press: the answer can arrive after the screen was composed. */
    sharedDevice: () -> Boolean,
    onGateSettled: () -> Unit,
) {
    val backStack by nav.currentBackStackEntryAsState()
    val route = backStack?.destination?.route
    val showRail = route != null && route !in FULL_SCREEN_ROUTES
    // Into the app with a fresh back stack: after pairing, after the picker, after a switch.
    val enterApp: () -> Unit = {
        if (nav.currentDestination?.route != Routes.HOME) {
            nav.navigate(Routes.HOME) { popUpTo(nav.graph.id) { inclusive = true }; launchSingleTop = true }
        }
    }
    var railExpanded by remember { mutableStateOf(false) }
    val railFocus = remember { FocusRequester() }
    val contentFocus = remember { FocusRequester() }
    val railController = remember { RailController { runCatching { railFocus.requestFocus() } } }
    val activity = LocalContext.current as? Activity
    // Time of the Back press on Home that showed the exit hint (0 = none); see BackPolicy.
    var exitArmedAt by remember { mutableLongStateOf(0L) }
    LaunchedEffect(route) { exitArmedAt = 0L }
    LaunchedEffect(exitArmedAt) { if (exitArmedAt != 0L) { delay(BackPolicy.EXIT_CONFIRM_MS); exitArmedAt = 0L } }

    // Back on a top-level screen: content → rail → Home → "Press Back again to exit" → exit.
    val onTopLevelBack: () -> Unit = {
        val now = SystemClock.elapsedRealtime()
        when (BackPolicy.decide(route, isTopLevel = true, railExpanded = railExpanded, lastBackAtMs = exitArmedAt, nowMs = now)) {
            BackAction.OpenRail -> { railController.openRail() }
            BackAction.GoHome -> {
                // Home is the bottom of every top-level stack; saveState keeps the left screen restorable from the rail.
                if (!nav.popBackStack(Routes.HOME, inclusive = false, saveState = true)) {
                    nav.navigate(Routes.HOME) { popUpTo(nav.graph.id) { inclusive = true }; launchSingleTop = true }
                }
                runCatching { contentFocus.requestFocus() }
            }
            BackAction.ConfirmExit -> { exitArmedAt = now }
            BackAction.Exit -> { activity?.finish() }
            BackAction.Default -> { nav.popBackStack() }
        }
    }

    CompositionLocalProvider(LocalRailController provides railController) {
        Box(Modifier.fillMaxSize()) {
            // Content is offset by the collapsed rail width; the expanded rail overlays it (no layout shift).
            Box(
                Modifier
                    .fillMaxSize()
                    .padding(start = if (showRail) RailMetrics.CollapsedW else 0.dp)
                    .focusRequester(contentFocus)
                    .focusRestorer(),
            ) {
                NavHost(nav, startDestination = start) {
                    composable(Routes.ONBOARDING) {
                        // Pairing has just read /auth/me: on a shared TV the next question is who is watching.
                        OnboardingScreen(onDone = {
                            onGateSettled()
                            nav.navigate(if (sharedDevice()) Routes.WHO_GATE else Routes.HOME) { popUpTo(Routes.ONBOARDING) { inclusive = true } }
                        })
                    }
                    composable(Routes.WHO_GATE) {
                        // No picker on this server or for this device (404 / 403): straight into the app.
                        WhoIsWatchingScreen(gate = true, onDone = enterApp, onAbsent = enterApp, onBack = {})
                    }
                    composable(Routes.WHO) {
                        WhoIsWatchingScreen(
                            gate = false,
                            onDone = enterApp,
                            // Asked for from the rail but the server has no picker: Settings has the profile list.
                            onAbsent = { nav.popBackStack(); nav.navigate(Routes.SETTINGS) { launchSingleTop = true } },
                            onBack = { nav.popBackStack() },
                        )
                    }
                    composable(Routes.HOME) { TopLevel(onTopLevelBack) { HomeScreen() } }
                    composable(Routes.SEARCH) {
                        TopLevel(onTopLevelBack) {
                            SearchScreen(onJoinRoom = if (access.can(Permissions.ROOMS_JOIN)) ({ nav.navigate(Routes.JOIN_ROOM) { launchSingleTop = true } }) else null)
                        }
                    }
                    composable(Routes.JOIN_ROOM) { TopLevel(onTopLevelBack) { JoinRoomScreen() } }
                    composable(Routes.RECORDINGS) { TopLevel(onTopLevelBack) { RecordingsScreen() } }
                    composable(Routes.REQUESTS) { RequestsScreen() }
                    composable(Routes.LIVE) { TopLevel(onTopLevelBack) { LiveDestination() } }
                    composable(Routes.SPORTS) { TopLevel(onTopLevelBack) { SportsScreen(onMultiview={nav.navigate(Routes.MULTIVIEW)}) } }
                    composable(Routes.MULTIVIEW) { MultiviewScreen(onExit={nav.popBackStack()}) }
                    composable(Routes.DISCOVER, arguments=listOf(navArgument("section"){type=NavType.StringType})) { entry ->
                        TopLevel(onTopLevelBack) {
                            DiscoveryScreen(onSearch={nav.navigate(Routes.SEARCH)},onLibrary={nav.navigate(if(entry.arguments?.getString("section")=="shows")Routes.SHOWS else Routes.MOVIES)},onSection={section->nav.navigate(Routes.discover(section)){popUpTo(Routes.DISCOVER){inclusive=true};launchSingleTop=true}})
                        }
                    }
                    composable(Routes.GUIDE) { TopLevel(onTopLevelBack) { LiveDestination(startInGuide = true) } }
                    composable(Routes.BROWSE, arguments = listOf(navArgument("section") { type = NavType.StringType })) { e ->
                        val section = e.arguments?.getString("section") ?: "movies"
                        TopLevel(onTopLevelBack) {
                            BrowseScreen(
                                title = if (section == "shows") "Shows" else "Movies",
                                onSeeAll = { category -> nav.navigate(Routes.browseAll(section, category)) },
                            )
                        }
                    }
                    composable(
                        Routes.BROWSE_ALL,
                        arguments = listOf(
                            navArgument("section") { type = NavType.StringType },
                            navArgument("category") { type = NavType.StringType; nullable = true },
                        ),
                    ) { e ->
                        BrowseScreen(title = if (e.arguments?.getString("section") == "shows") "Shows" else "Movies")
                    }
                    composable(Routes.FAVORITES) { TopLevel(onTopLevelBack) { FavoritesScreen() } }
                    composable(Routes.SOURCES) { TopLevel(onTopLevelBack) { SourcesScreen() } }
                    composable(Routes.SETTINGS) { TopLevel(onTopLevelBack) { SettingsScreen(
                                appVersion = BuildConfig.VERSION_NAME,
                                onWhoIsWatching = { nav.navigate(Routes.WHO) { launchSingleTop = true } },
                                onOpenRequests = { nav.navigate(Routes.REQUESTS) { launchSingleTop = true } },
                            ) } }
                    composable(Routes.DETAILS, arguments = listOf(navArgument("id") { type = NavType.StringType })) { DetailsScreen() }
                    composable(Routes.PLAYER, arguments = listOf(navArgument("key") { type = NavType.StringType })) {
                        PlayerScreen(onExit = { nav.popBackStack() })
                    }
                }
            }
            if (showRail) {
                LeftRail(
                    items = remember(access, recordings) { railItems(access, recordings) },
                    selectedRoute = when {
                        route == Routes.DISCOVER -> Routes.discover()
                        route == Routes.GUIDE -> Routes.LIVE
                        route == Routes.REQUESTS -> Routes.SETTINGS
                        // The destination route is the pattern, so Movies/Shows are told apart by the argument.
                        route?.startsWith("browse/") == true -> if (backStack?.arguments?.getString("section") == "shows") Routes.SHOWS else Routes.MOVIES
                        route?.startsWith("details") == true -> null
                        else -> route
                    },
                    expanded = railExpanded,
                    onExpandedChange = { railExpanded = it },
                    onSelect = { item ->
                        nav.navigate(item.route) {
                            // Home, not the graph's start destination: after first-run onboarding the start
                            // destination is no longer on the stack and top-level screens would pile up.
                            popUpTo(Routes.HOME) { saveState = true }
                            launchSingleTop = true
                            restoreState = true
                        }
                        runCatching { contentFocus.requestFocus() }
                    },
                    onExit = { runCatching { contentFocus.requestFocus() } },
                    profileName = profileName,
                    // The avatar is "Who's watching?" on a shared TV; elsewhere it leads to the profile list in Settings.
                    onProfile = { nav.navigate(if (sharedDevice()) Routes.WHO else Routes.SETTINGS) { launchSingleTop = true } },
                    firstItemRequester = railFocus,
                )
            }
            if (exitArmedAt != 0L) ExitHint(Modifier.align(Alignment.BottomCenter))
        }
    }
}

/**
 * A top-level destination. Its BackHandler is declared inside the destination and before the screen,
 * so it outranks NavHost's own pop (registered earlier) while a screen's own BackHandler (Guide →
 * Channels, a Sources form) is registered later and still wins.
 */
@Composable
private fun TopLevel(onBack: () -> Unit, content: @Composable () -> Unit) {
    BackHandler(onBack = onBack)
    content()
}

@Composable
private fun ExitHint(modifier: Modifier = Modifier) {
    val t = LocalThemeTokens.current
    Text(
        "Press Back again to exit",
        color = t.text,
        style = MaterialTheme.typography.bodyLarge,
        modifier = modifier
            .padding(bottom = Tokens.Canvas.SafeMarginY)
            .background(t.overlay, RoundedCornerShape(Tokens.Radius.Pill))
            .padding(horizontal = 20.dp, vertical = 10.dp),
    )
}
