package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.auth.SessionTokens
import com.streamhub.tv.core.data.auth.rotatedSince
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.RefreshRequest
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.HttpClientEngine
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.plugins.auth.Auth
import io.ktor.client.plugins.auth.providers.BearerTokens
import io.ktor.client.plugins.auth.providers.bearer
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.plugins.defaultRequest
import io.ktor.client.plugins.logging.LogLevel
import io.ktor.client.plugins.logging.Logger
import io.ktor.client.plugins.logging.Logging
import io.ktor.client.plugins.websocket.WebSockets
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json

val StreamHubJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    encodeDefaults = true
    coerceInputValues = true
}

/** Paths that must never carry a bearer token (security: [] in the contract). */
private val PUBLIC_PATHS = listOf(
    "/auth/login", "/auth/refresh", "/auth/device", "/auth/device/poll",
    "/disclaimer", "/themes", "/health",
    // GET /rooms/{code}/descriptor: the member token in the query is the credential.
    "/descriptor",
)

/**
 * Whether a failed POST /auth/refresh means this TV's pairing is gone. Only an explicit rejection of
 * the refresh token (400/401/403) does. A restarting or overloaded server answers 5xx, a proxy in
 * front of it 502/504, and an unreachable one not at all ([status] null): clearing the tokens then
 * would send a working TV back through pairing after every server restart.
 */
internal fun shouldClearSession(status: Int?, code: String? = null): Boolean =
    (status == 400 || status == 401 || status == 403) && code !in KEEPS_PAIRING

/**
 * Refusals that are about the person, not the token: a suspension can be lifted and an approval can
 * arrive, and the TV should then simply work again. The call that hit them reports the reason
 * (see ApiErrors) instead of the TV dropping back to pairing without a word.
 */
private val KEEPS_PAIRING = setOf("user_suspended", "approval_pending")

/**
 * Ktor CIO client with:
 *  - JSON via kotlinx.serialization
 *  - Bearer auth: tokens loaded from the session store; on 401 (an expired access token, or
 *    `session_stale` after a role or permission change) the refresh token is exchanged at
 *    POST /auth/refresh (marked as a refresh request so it is not itself retried), the new pair is
 *    persisted, [onTokensRefreshed] fires and the request is sent once more.
 *    If the server rejects the refresh token the session is cleared and [onSessionExpired] fires
 *    with the server's reason (`session_revoked`, or null) so the UI can say why and re-pair. A
 *    suspended user keeps the pairing and [onUserSuspended] fires instead; any other refresh
 *    failure keeps the session without a word (see [shouldClearSession]).
 *  - Base URL resolved per request from the session (server URL can change at runtime).
 */
fun createStreamHubHttpClient(
    sessionStore: SessionTokens,
    debug: Boolean,
    onSessionExpired: (code: String?) -> Unit,
    onTokensRefreshed: () -> Unit = {},
    onUserSuspended: () -> Unit = {},
    /** Tests pass a mock engine; the app uses CIO. */
    engine: HttpClientEngine? = null,
): HttpClient = HttpClient(engine ?: CIO.create()) {
    expectSuccess = false
    install(ContentNegotiation) { json(StreamHubJson) }
    // Watch rooms (see room/RoomClient); keep-alive is the room protocol's own ping, every 5 s.
    install(WebSockets)
    install(HttpTimeout) {
        connectTimeoutMillis = 8_000
        requestTimeoutMillis = 20_000
        socketTimeoutMillis = 20_000
    }
    if (debug) {
        install(Logging) {
            level = LogLevel.INFO
            logger = object : Logger {
                override fun log(message: String) {
                    // Redact bearer tokens and stream tokens from logs (LEGAL.md engineering rules).
                    android.util.Log.d("StreamHubHttp", message.replace(Regex("Bearer [A-Za-z0-9._-]+"), "Bearer ***"))
                }
            }
        }
    }
    install(Auth) {
        bearer {
            loadTokens {
                val s = sessionStore.current()
                if (s.accessToken != null && s.refreshToken != null) BearerTokens(s.accessToken, s.refreshToken) else null
            }
            refreshTokens {
                val original=sessionStore.current()
                val refresh = original.refreshToken
                // Refresh tokens rotate. If the pair this request used has been exchanged meanwhile (another
                // request's refresh, a switch, a new pairing), the stored pair is the current one: use it.
                // Presenting the old refresh token again would be a replay, which ends the session.
                if(rotatedSince(oldTokens?.refreshToken,refresh)) {
                    return@refreshTokens original.accessToken?.let { access -> refresh?.let { BearerTokens(access,it) } }
                }
                if (refresh == null) {
                    onSessionExpired(null); return@refreshTokens null
                }
                val base = original.apiBase ?: return@refreshTokens null
                // An I/O failure here (server restarting, network down) propagates to the caller as a
                // network error; the stored tokens are untouched and the next request tries again.
                val resp = client.post("$base/auth/refresh") {
                    markAsRefreshTokenRequest()
                    contentType(ContentType.Application.Json)
                    setBody(RefreshRequest(refresh))
                }
                if (!resp.status.isSuccess()) {
                    val code = runCatching { StreamHubJson.decodeFromString(ApiError.serializer(), resp.bodyAsText()).code }.getOrNull()
                    if (shouldClearSession(resp.status.value, code)) { if (sessionStore.clearExpiredSession(original)) onSessionExpired(code) }
                    else if (code == "user_suspended") onUserSuspended()
                    return@refreshTokens null
                }
                val tokens = resp.body<AuthTokens>()
                // The new refresh token is stored (with the access token, in one write) before the new access
                // token is used: the request that got the 401 (session_stale, or an expired access token) is
                // then sent once more with the new pair. A pair that could not be stored is not used.
                if(sessionStore.saveRefreshedTokens(original,tokens)) { onTokensRefreshed(); BearerTokens(tokens.accessToken, tokens.refreshToken) } else null
            }
            sendWithoutRequest { request ->
                val path = request.url.buildString().substringBefore("?")
                PUBLIC_PATHS.none { path.endsWith(it) }
            }
        }
    }
    defaultRequest {
        contentType(ContentType.Application.Json)
    }
}
