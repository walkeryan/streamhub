package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.RefreshRequest
import io.ktor.client.HttpClient
import io.ktor.client.call.body
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
import io.ktor.client.request.post
import io.ktor.client.request.setBody
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
)

/**
 * Ktor CIO client with:
 *  - JSON via kotlinx.serialization
 *  - Bearer auth: tokens loaded from [SessionStore]; on 401 the refresh token is exchanged at
 *    POST /auth/refresh (marked as a refresh request so it is not itself retried), new pair persisted.
 *    If refresh fails the session is cleared and [onSessionExpired] fires so the UI can re-pair.
 *  - Base URL resolved per request from the session (server URL can change at runtime).
 */
fun createStreamHubHttpClient(
    sessionStore: SessionStore,
    debug: Boolean,
    onSessionExpired: () -> Unit,
): HttpClient = HttpClient(CIO) {
    expectSuccess = false
    install(ContentNegotiation) { json(StreamHubJson) }
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
                if(oldTokens?.refreshToken!=null && oldTokens?.refreshToken!=refresh) {
                    return@refreshTokens original.accessToken?.let { access -> refresh?.let { BearerTokens(access,it) } }
                }
                if (refresh == null) {
                    onSessionExpired(); return@refreshTokens null
                }
                val base = original.apiBase ?: return@refreshTokens null
                val resp = client.post("$base/auth/refresh") {
                    markAsRefreshTokenRequest()
                    contentType(ContentType.Application.Json)
                    setBody(RefreshRequest(refresh))
                }
                if (!resp.status.isSuccess()) {
                    if(sessionStore.clearExpiredSession(original)) onSessionExpired()
                    return@refreshTokens null
                }
                val tokens = resp.body<AuthTokens>()
                if(sessionStore.saveRefreshedTokens(original,tokens)) BearerTokens(tokens.accessToken, tokens.refreshToken) else null
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
