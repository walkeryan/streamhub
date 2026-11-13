package com.streamhub.tv.shell

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.view.View
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.ShellEntry
import com.streamhub.tv.core.data.auth.shellEntry

/**
 * The full-screen WebView: no address bar, no zoom, no long-press menus or selection handles, no
 * pop-up windows, no file or content access. `useWideViewPort` lets the web app's viewport meta set
 * the layout width (it asks for 1920 CSS pixels in the shell, see web-app/src/shell.ts), so the TV
 * gets the layout a 1920×1080 desktop browser gets rather than the ~960 px a TV WebView reports.
 */
@SuppressLint("SetJavaScriptEnabled")
fun createShellWebView(context: Context): WebView = WebView(context).apply {
    with(settings) {
        javaScriptEnabled = true
        domStorageEnabled = true
        useWideViewPort = true
        loadWithOverviewMode = true
        setSupportZoom(false)
        builtInZoomControls = false
        displayZoomControls = false
        textZoom = 100
        setSupportMultipleWindows(false)
        javaScriptCanOpenWindowsAutomatically = false
        allowFileAccess = false
        allowContentAccess = false
        setGeolocationEnabled(false)
        // The web player never runs in the shell; nothing in the page should start sound on its own.
        mediaPlaybackRequiresUserGesture = true
    }
    isLongClickable = false
    setOnLongClickListener { true }
    isHapticFeedbackEnabled = false
    isVerticalScrollBarEnabled = false
    isHorizontalScrollBarEnabled = false
    overScrollMode = View.OVER_SCROLL_NEVER
    setBackgroundColor(Color.BLACK)
    isFocusable = true
    isFocusableInTouchMode = true
    // Focus stays in the page at its edges: the web app's own spatial navigation decides what is next.
    id = View.generateViewId()
    nextFocusUpId = id; nextFocusDownId = id; nextFocusLeftId = id; nextFocusRightId = id
    webChromeClient = object : WebChromeClient() {
        override fun onPermissionRequest(request: PermissionRequest) = request.deny()
    }
}

/**
 * What is in the WebView and what it is doing: writing a pair into the web app's storage
 * ([Phase.Seeding]), loading the web app, showing it, or failed. The activity calls [show] with
 * every session it sees; the controller loads only when what should be in the WebView changed.
 */
class ShellWebController(
    private val webView: WebView,
    /** The session as JSON for the web app (`webSessionJson`), from the stored pair. */
    private val sessionJson: (Session) -> String?,
    /** The seeded pair is in the web app: let the TV drop its refresh token. Answers whether it did. */
    private val handOver: (Session, (Boolean) -> Unit) -> Unit,
    private val onFailed: () -> Unit,
    private val onRendererGone: () -> Unit,
) {
    private sealed interface Phase {
        data object Idle : Phase
        data class Seeding(val session: Session, val json: String) : Phase
        data object Loading : Phase
        data object Ready : Phase
        data object Failed : Phase
    }

    private var phase: Phase = Phase.Idle
    /** The server whose web app is (being) loaded; null when the WebView holds nothing of use. */
    private var loadedServer: String? = null
    private var server: String? = null

    /** The web app is loaded and can answer (Back, token requests). */
    val ready: Boolean get() = phase == Phase.Ready

    fun show(session: Session) {
        val url = session.serverUrl ?: return
        server = url
        when (shellEntry(session)) {
            ShellEntry.Pair -> clear()
            ShellEntry.Seed -> if ((phase as? Phase.Seeding)?.session?.refreshToken != session.refreshToken) seed(session)
            ShellEntry.Web -> if (loadedServer != url || phase == Phase.Failed || phase == Phase.Idle) load(url)
        }
    }

    /** Retry: whatever was asked for last is asked for again. */
    fun reload(session: Session) { phase = Phase.Idle; loadedServer = null; show(session) }

    /** Signed out: nothing of the old session stays running in the page. */
    fun clear() {
        if (phase == Phase.Idle && loadedServer == null) return
        phase = Phase.Idle; loadedServer = null
        webView.loadUrl("about:blank")
    }

    private fun seed(session: Session) {
        val json = sessionJson(session) ?: return
        phase = Phase.Seeding(session, json)
        loadedServer = null
        // A blank document of the server's origin, so the session is written to that origin's storage
        // before the web app (which reads it on start) is loaded at all.
        webView.loadDataWithBaseURL(webAppUrl(session.serverUrl!!), SEED_PAGE, "text/html", "utf-8", null)
    }

    private fun load(serverUrl: String) {
        phase = Phase.Loading
        loadedServer = serverUrl
        webView.loadUrl(webAppUrl(serverUrl))
    }

    val client = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val s = server ?: return true
            // Only the paired server's pages load here; any other address loads nowhere.
            return !mayLoad(s, request.url.toString())
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            if (phase == Phase.Ready && url != null && server?.let { mayLoad(it, url) } == true) phase = Phase.Loading
        }

        override fun onPageFinished(view: WebView, url: String?) {
            when (val p = phase) {
                is Phase.Seeding -> view.evaluateJavascript(seedScript(p.json)) { answer ->
                    if (phase != p) return@evaluateJavascript
                    if (answer?.trim() != "true") { failed(); return@evaluateJavascript }
                    handOver(p.session) { done ->
                        if (phase != p) return@handOver
                        // Not handed over: the stored session changed meanwhile; the next show() decides again.
                        if (done) load(p.session.serverUrl!!) else phase = Phase.Idle
                    }
                }
                Phase.Loading -> if (url != null && server?.let { mayLoad(it, url) } == true) {
                    phase = Phase.Ready
                    // The seeding document is not somewhere Back should lead.
                    view.clearHistory()
                }
                else -> Unit
            }
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) failed()
        }

        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
            if (request.isForMainFrame && response.statusCode >= 400) failed()
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            phase = Phase.Failed
            onRendererGone()
            return true
        }
    }

    private fun failed() {
        if (phase == Phase.Idle) return
        phase = Phase.Failed
        onFailed()
    }

    private companion object {
        const val SEED_PAGE = "<!doctype html><html><head><meta charset=\"utf-8\"></head><body style=\"background:#000\"></body></html>"
    }
}
