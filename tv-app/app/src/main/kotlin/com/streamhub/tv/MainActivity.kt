package com.streamhub.tv

import android.content.Intent
import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.webkit.WebView
import android.widget.FrameLayout
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
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
import com.streamhub.tv.shell.PLAYBACK_ENDED_SCRIPT
import com.streamhub.tv.shell.ShellBridge
import com.streamhub.tv.shell.ShellScreen
import com.streamhub.tv.shell.ShellViewModel
import com.streamhub.tv.shell.ShellWebController
import com.streamhub.tv.shell.TvSettingsScreen
import com.streamhub.tv.shell.UnreachableScreen
import com.streamhub.tv.shell.backOutcome
import com.streamhub.tv.shell.createShellWebView
import com.streamhub.tv.shell.mayLoad
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
    private lateinit var webView: WebView
    private lateinit var composeView: ComposeView
    private lateinit var controller: ShellWebController
    private val capabilities by lazy { DeviceCapabilities.json(this) }
    /** The native player is in front: the page keeps running (it answers token requests) and is told when it closes. */
    private var playerOpen = false
    private var backPending = false

    private val playerLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        playerOpen = false
        // Progress shown on tiles and rows is read again; focus and screen are where they were.
        if (controller.ready) webView.evaluateJavascript(PLAYBACK_ENDED_SCRIPT, null)
    }

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
            ),
            ShellBridge.NAME,
        )
        tokenBroker.attach { id, rejected ->
            if (!controller.ready) false
            else { webView.post { webView.evaluateJavascript(tokenRequestScript(id, rejected), null) }; true }
        }

        composeView = ComposeView(this).apply { setContent { ShellContent() } }
        setContentView(FrameLayout(this).apply {
            addView(webView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            addView(composeView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        })

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.CREATED) {
                launch { vm.session.collect { s -> if (s != null) controller.show(s) } }
                launch { vm.screen.collect(::showScreen) }
            }
        }
    }

    /** The web app, or a native screen over it. Only one of them takes the remote's keys. */
    private fun showScreen(screen: ShellScreen) {
        val web = screen == ShellScreen.Web
        webView.visibility = if (web) View.VISIBLE else View.INVISIBLE
        webView.isFocusable = web
        composeView.descendantFocusability = if (web) ViewGroup.FOCUS_BLOCK_DESCENDANTS else ViewGroup.FOCUS_AFTER_DESCENDANTS
        if (web) webView.requestFocus()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
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
        if (vm.screen.value == ShellScreen.Web && !webView.hasFocus()) webView.requestFocus()
        return super.dispatchKeyEvent(event)
    }

    override fun onStart() {
        super.onStart()
        webView.onResume()
        webView.resumeTimers()
    }

    override fun onStop() {
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
        if (playerOpen) return
        playerOpen = true
        playerLauncher.launch(Intent(this, PlayerActivity::class.java).putExtra(PlayerActivity.EXTRA_KEY, key))
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
