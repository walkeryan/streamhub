package com.streamhub.tv.core.data.auth

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.Profile
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.serialization.json.Json
import javax.inject.Inject
import javax.inject.Singleton

private val Context.sessionStore: DataStore<Preferences> by preferencesDataStore(name = "session")

data class Session(
    val serverUrl: String?,
    val accessToken: String?,
    val refreshToken: String?,
    val profile: Profile?,
    val generation: String? = null,
    /**
     * The web app shown by the shell holds this TV's session (its refresh token lives in the web
     * app's storage, nowhere else). The TV keeps the server address, the person and the last access
     * token the web app handed over; it asks the web app for a current one and never refreshes.
     */
    val webOwned: Boolean = false,
) {
    val isPaired: Boolean get() = !serverUrl.isNullOrBlank() && !refreshToken.isNullOrBlank()

    /** API base, e.g. http://192.168.1.10:8787/api/v1 */
    val apiBase: String? get() = serverUrl?.let { normalizeServerUrl(it) + "/api/v1" }
}

/** Accepts "192.168.1.10", "192.168.1.10:8787", "http://host:8787/", "https://hub.example/api/v1". */
fun normalizeServerUrl(input: String): String {
    var s = input.trim().trimEnd('/')
    if (!s.startsWith("http://", ignoreCase = true) && !s.startsWith("https://", ignoreCase = true)) {
        s = "http://$s"
    }
    s = s.removeSuffix("/api/v1")
    val hostPart = s.substringAfter("://")
    if (!hostPart.substringBefore('/').contains(':') && s.startsWith("http://")) {
        s = s.replaceFirst(hostPart.substringBefore('/'), hostPart.substringBefore('/') + ":8787")
    }
    return s
}

/**
 * Persists server URL + JWT pair + active profile in DataStore (app-private storage).
 * Tokens are bearer credentials scoped to this device; nothing else (source passwords etc.)
 * ever reaches the TV — the server holds them.
 */
@Singleton
class SessionStore @Inject constructor(@ApplicationContext private val context: Context) : SessionTokens {
    private val json = Json { ignoreUnknownKeys = true }

    private object K {
        val generation = stringPreferencesKey("session_generation")
        val server = stringPreferencesKey("server_url")
        val access = stringPreferencesKey("access_token")
        val refresh = stringPreferencesKey("refresh_token")
        val profile = stringPreferencesKey("profile_json")
        val webOwned = stringPreferencesKey("web_session")
    }

    val session: Flow<Session> = context.sessionStore.data.map { p ->
        Session(
            generation = p[K.generation],
            serverUrl = p[K.server],
            accessToken = p[K.access],
            refreshToken = p[K.refresh],
            profile = p[K.profile]?.let { runCatching { json.decodeFromString(Profile.serializer(), it) }.getOrNull() },
            webOwned = p[K.webOwned] == "1",
        )
    }

    override suspend fun current(): Session = session.first()

    suspend fun setServerUrl(url: String) {
        context.sessionStore.edit {
            val normalized=normalizeServerUrl(url)
            if(it[K.server]!=normalized) it[K.generation]=java.util.UUID.randomUUID().toString()
            it[K.server]=normalized
        }
    }

    /** A new pairing: the TV holds the pair until it is handed over to the web app ([handOverToWeb]). */
    suspend fun saveTokens(tokens: AuthTokens) {
        context.sessionStore.edit {
            it.remove(K.webOwned)
            it[K.generation]=java.util.UUID.randomUUID().toString()
            it[K.access] = tokens.accessToken
            it[K.refresh] = tokens.refreshToken
            it[K.profile] = json.encodeToString(Profile.serializer(), tokens.profile)
        }
    }

    /** Atomic compare-and-replace prevents a late challenge from overwriting a newer sign-in/server. */
    suspend fun saveSwitchedTokens(expected: Session, targetId: String, tokens: AuthTokens) {
        require(tokens.profile.id==targetId) { "Profile switch target mismatch" }
        context.sessionStore.edit { values ->
            val current=Session(values[K.server],values[K.access],values[K.refresh],values[K.profile]?.let { json.decodeFromString(Profile.serializer(),it) },values[K.generation])
            if(!sameSwitchSession(expected,current)) throw kotlinx.coroutines.CancellationException("Profile session changed")
            values[K.generation]=java.util.UUID.randomUUID().toString()
            values[K.access]=tokens.accessToken
            values[K.refresh]=tokens.refreshToken
            values[K.profile]=json.encodeToString(Profile.serializer(),tokens.profile)
        }
    }

    /**
     * "Who's watching?": the tokens POST /auth/switch returned replace the session they were asked
     * from, and only that one. The generation is bumped, so everything bound to the previous person
     * (progress reports, stream leases, in-flight loads) stops matching.
     */
    suspend fun saveUserSwitchTokens(expected: Session, tokens: AuthTokens) {
        context.sessionStore.edit { values ->
            val current=Session(values[K.server],values[K.access],values[K.refresh],values[K.profile]?.let { json.decodeFromString(Profile.serializer(),it) },values[K.generation])
            if(!sameSwitchSession(expected,current)) throw kotlinx.coroutines.CancellationException("Session changed")
            values[K.generation]=java.util.UUID.randomUUID().toString()
            values[K.access]=tokens.accessToken
            values[K.refresh]=tokens.refreshToken
            values[K.profile]=json.encodeToString(Profile.serializer(),tokens.profile)
        }
    }

    /**
     * Refresh tokens rotate: the new refresh token replaces the stored one in the same write as the
     * new access token, before either is used. Refresh alone preserves identity; a stale response
     * must not replace a new login or a newer rotation (see [refreshedSession]).
     */
    override suspend fun saveRefreshedTokens(expected: Session, tokens: AuthTokens): Boolean {
        var saved=false
        context.sessionStore.edit { values ->
            val current=Session(values[K.server],values[K.access],values[K.refresh],values[K.profile]?.let { json.decodeFromString(Profile.serializer(),it) },values[K.generation])
            val next=refreshedSession(expected,current,tokens)
            if(next!=null) {
                values[K.generation]=next.generation!!
                values[K.access]=next.accessToken!!;values[K.refresh]=next.refreshToken!!
                values[K.profile]=json.encodeToString(Profile.serializer(),tokens.profile);saved=true
            }
        }
        return saved
    }
    override suspend fun clearExpiredSession(expected: Session): Boolean {
        var cleared=false
        context.sessionStore.edit { values ->
            val current=Session(values[K.server],values[K.access],values[K.refresh],values[K.profile]?.let { json.decodeFromString(Profile.serializer(),it) },values[K.generation])
            if(canApplyRefresh(expected,current,expected.profile?.id)) {
                values.remove(K.access);values.remove(K.refresh);values.remove(K.profile)
                values[K.generation]=java.util.UUID.randomUUID().toString();cleared=true
            }
        }
        return cleared
    }

    suspend fun updateProfile(profile: Profile) {
        context.sessionStore.edit { it[K.profile] = json.encodeToString(Profile.serializer(), profile) }
    }

    suspend fun clearTokens() {
        context.sessionStore.edit {
            it[K.generation]=java.util.UUID.randomUUID().toString()
            it.remove(K.access)
            it.remove(K.refresh)
            it.remove(K.profile)
            it.remove(K.webOwned)
        }
    }

    // ---- The web app owns the session ----------------------------------------------------------

    /**
     * The pair [expected] held has been written into the web app's storage: from now on the web app
     * refreshes and rotates it. The refresh token is dropped here (one owner, never two refreshing
     * copies); the server address, the person, the access token and the session's identity stay.
     * False when the stored session is no longer the one that was seeded.
     */
    suspend fun handOverToWeb(expected: Session): Boolean {
        var done=false
        context.sessionStore.edit { values ->
            val next=handedOverSession(expected,readSession(values)) ?: return@edit
            values[K.generation]=next.generation!!
            values.remove(K.refresh)
            values[K.webOwned]="1"
            done=true
        }
        return done
    }

    /** Stores the access token (and the person) the web app answered with; see [adoptedWebSession]. */
    suspend fun adoptWebToken(token: WebToken): Boolean {
        var done=false
        context.sessionStore.edit { values ->
            val next=adoptedWebSession(readSession(values),token) { java.util.UUID.randomUUID().toString() } ?: return@edit
            values[K.generation]=next.generation!!
            values[K.access]=token.access
            values[K.profile]=json.encodeToString(Profile.serializer(),token.profile)
            done=true
        }
        return done
    }

    private fun readSession(values: Preferences) = Session(
        values[K.server], values[K.access], values[K.refresh],
        values[K.profile]?.let { runCatching { json.decodeFromString(Profile.serializer(), it) }.getOrNull() },
        values[K.generation], values[K.webOwned] == "1",
    )
}
