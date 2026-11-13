package com.streamhub.tv.core.data.auth

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.Profile
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/** What the web app answered when the TV asked for a current access token: the token and whose it is. */
data class WebToken(val access: String, val profile: Profile) {
    override fun toString(): String = "WebToken(access=[REDACTED], profile=${profile.id})"
}

/** Where the HTTP client gets an access token once the web app owns the session. */
interface AccessTokenSource {
    /**
     * A current access token, or null when there is none (the web app is signed out or not loaded).
     * [rejected] is the token the server just refused: the web app refreshes when it still holds that
     * one (or its own has expired) and otherwise answers with what it holds.
     */
    suspend fun currentAccessToken(rejected: String?): String?
}

/** What [shellEntry] decides the shell shows on start. */
enum class ShellEntry {
    /** No server, or no session: the native pairing screens. */
    Pair,
    /** A pair the TV holds (a pairing just made, or an install from before the shell): written into the web app first. */
    Seed,
    /** The web app holds the session. */
    Web,
}

fun shellEntry(s: Session): ShellEntry = when {
    s.serverUrl.isNullOrBlank() -> ShellEntry.Pair
    !s.refreshToken.isNullOrBlank() && s.profile != null -> ShellEntry.Seed
    s.webOwned && s.profile != null -> ShellEntry.Web
    else -> ShellEntry.Pair
}

/**
 * The session after the pair [expected] held was seeded into the web app: no refresh token, owned by
 * the web app, the same identity (a legacy session without a generation gets the one derived from
 * its refresh token, so everything bound to it carries on). Null when [current] is no longer that pair.
 */
fun handedOverSession(expected: Session, current: Session): Session? {
    if (expected.refreshToken.isNullOrBlank() || expected.refreshToken != current.refreshToken || expected.serverUrl != current.serverUrl) return null
    return current.copy(refreshToken = null, webOwned = true, generation = switchSessionGeneration(current))
}

/**
 * The session after the web app answered [token]. Only a session the web app owns takes one. The
 * same person keeps the session's identity (progress reports and stream leases carry on); another
 * person (switched in the web app) is a new session, so nothing bound to the previous one continues.
 */
fun adoptedWebSession(current: Session, token: WebToken, newGeneration: () -> String): Session? {
    if (!current.webOwned || current.serverUrl.isNullOrBlank()) return null
    val generation = if (current.profile?.id == token.profile.id) current.generation ?: newGeneration() else newGeneration()
    return current.copy(accessToken = token.access, profile = token.profile, generation = generation)
}

/** An access token as the bridge may carry it: printable ASCII without spaces, of a sane length. */
internal fun plausibleToken(value: String): Boolean = value.length in 8..8192 && value.all { it in '!'..'~' }

/**
 * Reads the web app's answer to a token request: `{"access": "…", "profile": {…}}`, or `null` when
 * it has no session. Anything else (a wrong type, a missing person, a token with spaces) is null too.
 */
fun parseWebToken(json: String?): WebToken? {
    if (json == null || json.length > 65_536) return null
    val root = runCatching { StreamHubJson.parseToJsonElement(json) }.getOrNull() ?: return null
    if (root is JsonNull || root !is JsonObject) return null
    val access = (root["access"] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: return null
    if (!plausibleToken(access)) return null
    val profileJson = root["profile"] as? JsonObject ?: return null
    val profile = runCatching { StreamHubJson.decodeFromJsonElement(Profile.serializer(), profileJson.jsonObject) }.getOrNull() ?: return null
    if (profile.id.isBlank() || profile.id.length > 256) return null
    return WebToken(access, profile)
}

/**
 * Native → web token requests. The shell [attach]es a channel that asks the page (it calls
 * `window.streamhubShell.token(id, rejected)`); the page answers through the bridge with the same
 * id, which [deliver] matches to the waiting request. Ids are random and single-use, so only an
 * answer to a question this TV asked is taken.
 */
@Singleton
class WebTokenBroker internal constructor(private val timeoutMs: Long) {
    @Inject constructor() : this(TIMEOUT_MS)

    private val pending = ConcurrentHashMap<String, CompletableDeferred<WebToken?>>()
    @Volatile private var channel: ((id: String, rejected: String?) -> Boolean)? = null
    private val random = SecureRandom()

    /** [ask] sends the request to the page; false when there is no page to ask. */
    fun attach(ask: (id: String, rejected: String?) -> Boolean) { channel = ask }
    fun detach() { channel = null; pending.values.forEach { it.complete(null) }; pending.clear() }

    suspend fun request(rejected: String?): WebToken? {
        val ask = channel ?: return null
        val id = ByteArray(16).also(random::nextBytes).joinToString("") { "%02x".format(it) }
        val answer = CompletableDeferred<WebToken?>()
        pending[id] = answer
        return try {
            if (!ask(id, rejected)) null else withTimeoutOrNull(timeoutMs) { answer.await() }
        } finally { pending.remove(id) }
    }

    /** The page's answer to request [id] (raw JSON from the bridge). False for an id nobody is waiting on. */
    fun deliver(id: String, json: String?): Boolean = pending.remove(id)?.complete(parseWebToken(json)) ?: false

    private companion object { const val TIMEOUT_MS = 15_000L }
}

/** The app's [AccessTokenSource]: asks the web app and keeps what it answers as this TV's session. */
@Singleton
class WebSessionTokens @Inject constructor(
    private val broker: WebTokenBroker,
    private val store: SessionStore,
) : AccessTokenSource {
    override suspend fun currentAccessToken(rejected: String?): String? {
        val token = broker.request(rejected) ?: return null
        return if (store.adoptWebToken(token)) token.access else null
    }
}
