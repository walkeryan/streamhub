package com.streamhub.tv.shell

import android.os.Handler
import android.os.Looper
import android.util.Log
import android.webkit.JavascriptInterface
import com.streamhub.tv.core.data.auth.WebTokenBroker

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
