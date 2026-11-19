package com.streamhub.tv.shell

import android.os.Handler
import android.os.Looper
import android.util.Log
import android.webkit.JavascriptInterface
import com.streamhub.tv.core.data.auth.WebTokenBroker
import com.streamhub.tv.feature.player.MiniPlayerCommand

/**
 * `window.StreamHubTV`: added with addJavascriptInterface before the first page loads, so the web app
 * finds it at document start. Calls arrive on a WebView thread; everything is checked here
 * ([parseShellPlay], WebTokenBroker) and then handed to the main thread. Nothing that arrives from
 * the page is trusted as an address: playback is resolved through the API by the TV itself.
 */
class ShellBridge(
    private val tokens: WebTokenBroker,
    private val capabilities: () -> String,
    /** Main thread. Whether the page that called is the paired server's (see [mayLoad]). */
    private val pageAllowed: () -> Boolean,
    private val onPlay: (ShellPlay) -> Unit,
    private val onSignedOut: (reason: String?) -> Unit,
    /** Main thread. A mini player bar button; true when a player in picture-in-picture took it. */
    private val onMiniPlayerCommand: (MiniPlayerCommand) -> Boolean = { false },
    /** Main thread. The page's microphone button: start or stop listening. */
    private val onVoiceCommand: (VoiceCommand) -> Unit = {},
    /** Main thread. What the page wants the picture-in-picture window to stay off (see PipLayout.kt). */
    private val onKeepClear: (KeepClearReport) -> Unit = {},
) {
    private val main = Handler(Looper.getMainLooper())

    @JavascriptInterface
    fun play(json: String?): Boolean {
        val parsed = json?.let(::parseShellPlay) ?: ParsedPlay.Rejected("empty")
        if (parsed !is ParsedPlay.Ok) { Log.w(TAG, "play() refused: ${(parsed as ParsedPlay.Rejected).reason}"); return false }
        main.post { if (pageAllowed()) onPlay(parsed.play) }
        return true
    }

    @JavascriptInterface
    fun signedOut(reason: String?) {
        val said = reason?.trim()?.take(300)?.filter { it >= ' ' }?.takeIf { it.isNotEmpty() }
        main.post { if (pageAllowed()) onSignedOut(said) }
    }

    /**
     * A `{"v":1,"kind":…}` message from the page: `mini_player_command`, the web app's mini player bar
     * (see [parseMiniPlayerCommand]), `voice_command`, its microphone button (see [parseVoiceCommand]), or
     * `keep_clear`, the page's rects for the picture-in-picture window to avoid (see [parseKeepClear]).
     * False when refused or of an unknown kind.
     */
    @JavascriptInterface
    fun message(json: String?): Boolean {
        if (json == null) { Log.w(TAG, "message() refused"); return false }
        parseMiniPlayerCommand(json)?.let { command ->
            main.post { if (pageAllowed()) onMiniPlayerCommand(command) }
            return true
        }
        parseVoiceCommand(json)?.let { command ->
            main.post { if (pageAllowed()) onVoiceCommand(command) }
            return true
        }
        parseKeepClear(json)?.let { report ->
            main.post { if (pageAllowed()) onKeepClear(report) }
            return true
        }
        Log.w(TAG, "message() refused")
        return false
    }

    @JavascriptInterface
    fun capabilities(): String = capabilities.invoke()

    /** The answer to `window.streamhubShell.token(id, …)`; only ids the TV issued are taken. */
    @JavascriptInterface
    fun deliverToken(id: String?, json: String?) {
        if (id == null || id.length != 32) return
        tokens.deliver(id, json)
    }

    companion object {
        const val NAME = "StreamHubTV"
        private const val TAG = "StreamHubShell"
    }
}
