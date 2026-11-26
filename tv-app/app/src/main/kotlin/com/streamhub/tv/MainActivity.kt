package com.streamhub.tv

import android.content.Intent
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.webkit.WebView
import android.widget.FrameLayout
import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.activity.viewModels
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.ComposeView
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.auth.WebTokenBroker
import com.streamhub.tv.core.data.handoff.HandoffResult
import com.streamhub.tv.core.data.repo.PlayEvent
import com.streamhub.tv.core.ui.components.AudioSourceDialog
import com.streamhub.tv.core.ui.components.ErrorDialog
import com.streamhub.tv.core.ui.components.HandoffQrDialog
import com.streamhub.tv.core.ui.components.ScreenBackground
import com.streamhub.tv.core.ui.theme.StreamHubTheme
import com.streamhub.tv.core.ui.theme.ThemeRepository
import com.streamhub.tv.feature.onboarding.OnboardingScreen
import com.streamhub.tv.shell.BACK_SCRIPT
import com.streamhub.tv.shell.BackOutcome
import com.streamhub.tv.shell.KeepClearReport
import com.streamhub.tv.shell.PLAYBACK_ENDED_SCRIPT
import com.streamhub.tv.shell.ShellBridge
import com.streamhub.tv.shell.NativePlayer
import com.streamhub.tv.shell.PlayerLink
import com.streamhub.tv.shell.PxRect
import com.streamhub.tv.shell.miniPlayerEventJson
import com.streamhub.tv.shell.miniPlayerScript
import com.streamhub.tv.shell.VoiceCommand
import com.streamhub.tv.shell.imeEventJson
import com.streamhub.tv.shell.voiceEventJson
import com.streamhub.tv.feature.player.MiniPlayerCommand
import com.streamhub.tv.shell.ShellScreen
import com.streamhub.tv.shell.ShellViewModel
import com.streamhub.tv.shell.ShellWebController
import com.streamhub.tv.shell.TvSettingsScreen
import com.streamhub.tv.shell.UpdatePromptDialog
import com.streamhub.tv.shell.UpdatePromptViewModel
import com.streamhub.tv.shell.UnreachableScreen
import com.streamhub.tv.shell.backOutcome
import com.streamhub.tv.shell.createShellWebView
import com.streamhub.tv.shell.keepClearPx
import com.streamhub.tv.shell.mayLoad
import com.streamhub.tv.shell.pipInCss
import com.streamhub.tv.shell.tokenRequestScript
import com.streamhub.tv.shell.webSessionJson
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * The shell: the paired server's web app, full screen, in a WebView. Everything a person browses is
 * the web app, so every change to it reaches the TV at once. Native are only pairing (server address,
 * device code), "Cannot reach your server", the TV's own settings, and playback: Play in the web app
 * comes here through the bridge ([ShellBridge]) and opens [PlayerActivity].
 *
 * The WebView lives as long as the activity, which handles configuration changes itself (manifest),
 * so neither a change of display mode nor a trip to the player or the home screen reloads the page.
 */
@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    @Inject lateinit var themes: ThemeRepository
    @Inject lateinit var tokenBroker: WebTokenBroker

    private val vm: ShellViewModel by viewModels()
    private val updatePrompt: UpdatePromptViewModel by viewModels()
    private lateinit var webView: WebView
    private lateinit var composeView: ComposeView
    private lateinit var controller: ShellWebController
    private val capabilities by lazy { DeviceCapabilities.json(this) }

    private val speech by lazy { VoiceSearch(this) { state, words, error ->
        sendToPage(miniPlayerScript(voiceEventJson(state, words, error)))
    } }
    private var awaitingMic = false
    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (awaitingMic) {
            awaitingMic = false
            if (!granted) sendToPage(miniPlayerScript(voiceEventJson("error", error = "permission")))
            else if (vm.screen.value == ShellScreen.Web && controller.ready) speech.start()
        }
    }
    private var imeVisible = false

    private fun voice(command: VoiceCommand) {
        if (command == VoiceCommand.Stop) {
            if (awaitingMic) {
                awaitingMic = false
                sendToPage(miniPlayerScript(voiceEventJson("idle")))
            }
            speech.stop()
        } else if (!awaitingMic && !speech.listening) {
            if (!VoiceSearch.available(this)) {
                sendToPage(miniPlayerScript(voiceEventJson("error", error = "unavailable")))
            } else if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                speech.start()
            } else {
                awaitingMic = true
                micPermission.launch(Manifest.permission.RECORD_AUDIO)
            }
        }
    }

    /**
     * The native player is open, full screen or as the mini player (see [PlayerLink]): the page keeps
     * running (it answers token requests) and is told when it closes.
     */
    private val playerOpen: Boolean get() = launching || PlayerLink.state.value != NativePlayer.None
    /** A player was started and has not said it is up yet. */
    private var launching = false
    private var backPending = false
    /** What the page last asked the picture-in-picture window to stay off (its `keep_clear` message). */
    private var keepClear: KeepClearReport? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        webView = createShellWebView(this)
        controller = ShellWebController(
            webView,
            sessionJson = { s -> s.refreshToken?.let { refresh -> s.profile?.let { webSessionJson(s.accessToken.orEmpty(), refresh, it) } } },
            handOver = { s, done -> lifecycleScope.launch { done(vm.handOver(s)) } },
            onFailed = vm::pageFailed,
            // The page's renderer died (memory pressure): a new activity brings a new WebView.
            onRendererGone = { recreate() },
        )
        webView.webViewClient = controller.client
        webView.addJavascriptInterface(
            ShellBridge(
                tokens = tokenBroker,
                capabilities = { capabilities },
                pageAllowed = { val s = vm.session.value?.serverUrl; val url = webView.url; s != null && url != null && mayLoad(s, url) },
                onPlay = vm::play,
                onSignedOut = vm::signedOut,
                onMiniPlayerCommand = PlayerLink::command,
                onVoiceCommand = ::voice,
                onKeepClear = { report -> keepClear = report; applyKeepClear() },
            ),
            ShellBridge.NAME,
        )
        tokenBroker.attach { id, rejected ->
            if (!controller.ready) false
            else { webView.post { webView.evaluateJavascript(tokenRequestScript(id, rejected), null) }; true }
        }

        // The page edits a field only after OK (it never takes the caret as focus passes over it); when the
        // on-screen keyboard closes, by its Done key or Back, the page is told so editing ends there too.
        ViewCompat.setOnApplyWindowInsetsListener(webView) { view, insets ->
            val visible = insets.isVisible(WindowInsetsCompat.Type.ime())
            if (visible != imeVisible) { imeVisible = visible; sendToPage(miniPlayerScript(imeEventJson(visible))) }
            ViewCompat.onApplyWindowInsets(view, insets)
        }

        composeView = ComposeView(this).apply { setContent { ShellContent() } }
        setContentView(FrameLayout(this).apply {
            addView(webView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            addView(composeView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        })

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.CREATED) {
                launch { vm.session.collect { s -> if (s != null) controller.show(s) } }
                launch { vm.screen.collect { showScreen(it); checkForUpdate() } }
                launch { followPlayer() }
            }
        }
    }

    /**
     * The native player as [PlayerLink] reports it. While it is the mini player the web app gets a
     * `mini_player` event with every change (about once a second) and draws its bar; when it stops
     * being one the event says null. When the player closes, the page reads progress again.
     */
    private suspend fun followPlayer() {
        var last: NativePlayer = NativePlayer.None
        PlayerLink.state.collect { now ->
            if (now != NativePlayer.None) launching = false
            if (now is NativePlayer.Mini) sendToPage(miniPlayerScript(miniPlayerEventJson(now.info, now.bounds?.let(::pipForPage), KEEP_CLEAR_SUPPORTED)))
            else if (last is NativePlayer.Mini) sendToPage(miniPlayerScript(miniPlayerEventJson(null)))
            // Keep-clear areas only mean something while the picture-in-picture window is up.
            if ((now is NativePlayer.Mini) != (last is NativePlayer.Mini)) applyKeepClear()
            if (now == NativePlayer.None && last != NativePlayer.None) {
                // Progress shown on tiles and rows is read again; focus and screen are where they were.
                sendToPage(PLAYBACK_ENDED_SCRIPT)
                checkForUpdate() // a check that came due while the player was up
            }
            last = now
        }
    }

    private fun sendToPage(script: String) { if (controller.ready) webView.evaluateJavascript(script, null) }

    /**
     * While the native player is in picture-in-picture, the WebView declares the page's keep-clear rects
     * (the focused element, the mini player bar) so the system's PiP placement tries to stay off them
     * (Android 13+; best effort, see PipLayout.kt). Otherwise it declares none.
     */
    private fun applyKeepClear() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val report = keepClear.takeIf { PlayerLink.state.value is NativePlayer.Mini && webView.visibility == View.VISIBLE }
        @Suppress("DEPRECATION") // getScale(): only the fallback when the page did not give its viewport width
        val rects = report?.let { keepClearPx(it, webView.width, webView.height, webView.scale) }.orEmpty()
        webView.setPreferKeepClearRects(rects.map { Rect(it.left, it.top, it.right, it.bottom) })
    }

    /** The picture-in-picture window in the page's CSS px, for the page's own fallback (web-app/src/player/pipAvoid.ts). */
    private fun pipForPage(bounds: PxRect) = IntArray(2).let { at ->
        webView.getLocationOnScreen(at)
        @Suppress("DEPRECATION")
        pipInCss(bounds, at[0], at[1], webView.width, webView.height, keepClear?.viewportWidth, webView.scale)
    }

    /** The web app, or a native screen over it. Only one of them takes the remote's keys. */
    private fun showScreen(screen: ShellScreen) {
        val web = screen == ShellScreen.Web
        webView.visibility = if (web) View.VISIBLE else View.INVISIBLE
        webView.isFocusable = web
        composeView.descendantFocusability = if (web) ViewGroup.FOCUS_BLOCK_DESCENDANTS else ViewGroup.FOCUS_AFTER_DESCENDANTS
        if (web) webView.requestFocus()
    }

    /** The system's search request (some remotes' search key): StreamHub's own voice search. */
    override fun onSearchRequested(): Boolean {
        if (vm.screen.value != ShellScreen.Web || !controller.ready) return false
        voice(VoiceCommand.Start)
        return true
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.keyCode == KeyEvent.KEYCODE_BACK && speech.listening) {
            if (event.action == KeyEvent.ACTION_UP) speech.stop()
            return true
        }
        if (event.keyCode == KeyEvent.KEYCODE_BACK && vm.screen.value == ShellScreen.Web) {
            // Back is the web app's: it closes a dialog or goes back, and on its home screen says no,
            // and then the app is left like any TV app. Repeats while held are ignored.
            if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0 && !backPending) {
                if (!controller.ready) { finish(); return true }
                backPending = true
                webView.evaluateJavascript(BACK_SCRIPT) { answer ->
                    backPending = false
                    if (backOutcome(answer) == BackOutcome.Exit) finish()
                }
            }
            return true
        }
        // The remote's search keys, when the system passes them on: StreamHub's own voice search.
        if (VoiceSearch.isSearchKey(event.keyCode) && vm.screen.value == ShellScreen.Web && controller.ready) {
            if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) voice(VoiceCommand.Start)
            return true
        }
        // The remote's play/pause and seek keys drive the mini player while it is up.
        if (PlayerLink.state.value is NativePlayer.Mini) miniPlayerKey(event.keyCode)?.let { c ->
            if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) PlayerLink.command(c)
            return true
        }
        if (vm.screen.value == ShellScreen.Web && !webView.hasFocus()) webView.requestFocus()
        return super.dispatchKeyEvent(event)
    }

    override fun onStart() {
        super.onStart()
        // A player that was started but never came up (nothing to play) does not block the next one.
        if (PlayerLink.state.value == NativePlayer.None) launching = false
        webView.onResume()
        webView.resumeTimers()
        checkForUpdate()
    }

    /** Asks the server for a newer build when the web app is in front (see UpdatePromptRules for how often). */
    private fun checkForUpdate() = updatePrompt.checkIfDue(onWebApp = vm.screen.value == ShellScreen.Web, playerOpen = playerOpen)

    override fun onStop() {
        voice(VoiceCommand.Stop)
        // Backgrounded: the page rests. Behind the native player it keeps running, to answer token requests.
        if (!playerOpen) { webView.onPause(); webView.pauseTimers() }
        super.onStop()
    }

    override fun onDestroy() {
        tokenBroker.detach()
        (webView.parent as? ViewGroup)?.removeView(webView)
        webView.destroy()
        super.onDestroy()
    }

    private fun openPlayer(key: String) {
        if (launching || PlayerLink.state.value == NativePlayer.FullScreen) return
        // One mini player at a time: a new title replaces the one in picture-in-picture.
        PlayerLink.closeOpenPlayer()
        launching = true
        startActivity(Intent(this, PlayerActivity::class.java).putExtra(PlayerActivity.EXTRA_KEY, key))
    }

    private companion object {
        /** View.setPreferKeepClearRects exists from Android 13 (API 33). */
        val KEEP_CLEAR_SUPPORTED = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
    }

    private fun miniPlayerKey(code: Int): MiniPlayerCommand? = when (code) {
        KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_MEDIA_PLAY, KeyEvent.KEYCODE_MEDIA_PAUSE -> MiniPlayerCommand.PlayPause
        KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> MiniPlayerCommand.SeekBy(30_000)
        KeyEvent.KEYCODE_MEDIA_REWIND -> MiniPlayerCommand.SeekBy(-10_000)
        KeyEvent.KEYCODE_MEDIA_NEXT -> MiniPlayerCommand.Next
        KeyEvent.KEYCODE_MEDIA_PREVIOUS -> MiniPlayerCommand.Previous
        else -> null
    }

    @Composable
    private fun ShellContent() {
        val selected by themes.selectedThemeId.collectAsStateWithLifecycle(ThemeRepository.DEFAULT_THEME)
        val screen by vm.screen.collectAsStateWithLifecycle()
        val session by vm.session.collectAsStateWithLifecycle()
        StreamHubTheme(tokens = themes.tokens(selected)) {
            when (screen) {
                ShellScreen.Loading -> ScreenBackground {}
                ShellScreen.Pairing -> key(vm.pairingRun) {
                    OnboardingScreen(onDone = vm::paired, startAtServer = vm.pairingAtServer, vm = hiltViewModel(key = "onboarding-${vm.pairingRun}"))
                }
                ShellScreen.Unreachable -> UnreachableScreen(
                    session?.serverUrl,
                    onRetry = { vm.retry(); session?.let(controller::reload) },
                    onChangeServer = vm::changeServer,
                    onSettings = vm::openSettings,
                )
                ShellScreen.Settings -> TvSettingsScreen(BuildConfig.VERSION_NAME, onBack = vm::closeSettings, onChangeServer = vm::changeServer, onSignOut = vm::signOutHere)
                ShellScreen.Web -> Unit
            }
            PlaybackDialogs()
            val update by updatePrompt.state.collectAsStateWithLifecycle()
            UpdatePromptDialog(update, BuildConfig.VERSION_NAME, onInstall = updatePrompt::install, onLater = updatePrompt::later)
            val notice by vm.signedOutNotice.collectAsState()
            notice?.let { ErrorDialog(UserError("Signed out", it, canRetry = false), onRetry = {}, onDismiss = { vm.signedOutNotice.value = null }) }
        }
    }

    /** What a press of Play can lead to besides the player: an audio choice, another app's QR code, a refusal. */
    @Composable
    private fun PlaybackDialogs() {
        var audioChoice by remember { mutableStateOf<PlayEvent.ChooseAudio?>(null) }
        var playError by remember { mutableStateOf<UserError?>(null) }
        var qr by remember { mutableStateOf<HandoffResult.ShowQr?>(null) }
        LaunchedEffect(Unit) {
            vm.coordinator.events.collect { e ->
                when (e) {
                    is PlayEvent.ChooseAudio -> { playError = null; audioChoice = e }
                    is PlayEvent.StartPlayer -> { playError = null; openPlayer(e.key) }
                    is PlayEvent.Handoff -> if (e.result is HandoffResult.ShowQr) qr = e.result as HandoffResult.ShowQr
                    is PlayEvent.Error -> { audioChoice = null; playError = e.error }
                }
            }
        }
        audioChoice?.let { choice ->
            AudioSourceDialog(choice.options, onDismiss = { audioChoice = null; vm.coordinator.cancelPending() }) { variant, confirmed ->
                audioChoice = null
                vm.coordinator.chooseAudio(choice.request, variant, confirmed)
            }
        }
        qr?.let { HandoffQrDialog(it.webUrl, it.serviceName) { qr = null } }
        playError?.let { error ->
            ErrorDialog(error, onRetry = { playError = null; vm.coordinator.retryLast() }, onDismiss = { playError = null; vm.coordinator.dismissError() })
        }
    }
}
