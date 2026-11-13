package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.Profile
import com.streamhub.tv.core.data.model.SwitchableUser
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import javax.inject.Inject
import javax.inject.Singleton

/** How an attempt to become another person on this TV ended. */
sealed interface SwitchResult {
    /** Signed in as [profile]; tokens are saved and the previous person's caches are gone. */
    data class Ok(val profile: Profile) : SwitchResult
    /** This person has a PIN and none was sent. */
    data object PinRequired : SwitchResult
    data object PinInvalid : SwitchResult
    /** This person must set a PIN (on the web) before they can be picked on a shared TV. */
    data object PinSetupRequired : SwitchResult
    /** Too many wrong PINs; nothing is accepted for [retryAfterSeconds]. */
    data class LockedOut(val retryAfterSeconds: Int) : SwitchResult
    data class Failed(val error: UserError) : SwitchResult
}

/** What [UserSwitchRepository] needs from the rest of the app; faked in tests. */
internal interface SwitchBackend {
    suspend fun switchable(): List<SwitchableUser>
    /** POST /auth/switch from the current session. A 401 comes back as it is: it is about the PIN. */
    suspend fun post(userId: String, pin: String?): AuthTokens
    /** Exchanges the token pair, for the one retry after `session_stale` / `token_expired`. */
    suspend fun refreshSession()
    /** Stops playback and drops what was cached for the person who is leaving. */
    suspend fun release()
    /** Saves [tokens] over the session [post] was sent from and bumps the session generation. */
    suspend fun save(tokens: AuthTokens)
    /** Legacy profile switch: saves [tokens] over [original], checked against [targetProfileId]. */
    suspend fun saveProfile(original: Session, targetProfileId: String, tokens: AuthTokens)
    /** Forgets the previous person's access and reads the new person's. */
    suspend fun reloadAccess()
}

/**
 * "Who's watching?": the people who may be picked on this shared TV, and becoming one of them.
 * Also the one place a switch is committed (tokens saved, session generation bumped, per-user
 * caches cleared), which the older per-profile switch in Settings goes through as well.
 */
@Singleton
class UserSwitchRepository internal constructor(private val backend: SwitchBackend) {
    @Inject constructor(api: StreamHubApi, session: SessionStore, playback: PlaybackCoordinator, channels: ChannelListCache, me: MeRepository) :
        this(DefaultSwitchBackend(api, session, playback, channels, me))

    private val mutex = Mutex()

    /**
     * The people who can be picked here, or null when there is no picker: a server without the
     * feature (404) or a personal device (403 `not_shared_device`). Other failures are thrown.
     */
    suspend fun switchable(): List<SwitchableUser>? =
        try { backend.switchable() }
        catch (e: CancellationException) { throw e }
        catch (e: ApiException) { if (e.status == 404 || e.status == 403) null else throw e }

    suspend fun switchTo(userId: String, pin: String?): SwitchResult = mutex.withLock {
        val tokens = try {
            try { backend.post(userId, pin) }
            catch (e: ApiException) {
                // The switch call keeps the usual 401 handling out (a 401 here normally means the PIN),
                // so a stale or expired session is refreshed here, once, and the call repeated.
                if (e.status != 401 || e.error?.code !in REFRESHABLE) throw e
                backend.refreshSession()
                backend.post(userId, pin)
            }
        } catch (e: CancellationException) { throw e }
        catch (e: ApiException) { return@withLock mapSwitchFailure(e) }
        catch (e: Exception) { return@withLock SwitchResult.Failed(ApiErrors.describe(e)) }
        try {
            backend.release()
            backend.save(tokens)
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { return@withLock SwitchResult.Failed(ApiErrors.describe(e)) }
        // The switch itself is done; a slow or failed read of the new person's access must not undo it.
        try { withTimeoutOrNull(ACCESS_WAIT_MS) { backend.reloadAccess() } }
        catch (e: CancellationException) { throw e } catch (_: Exception) { }
        SwitchResult.Ok(tokens.profile)
    }

    /** Before a legacy profile switch is authorized, while the old profile can still give back its stream. */
    suspend fun releasePlayback() = backend.release()

    /** Commits a legacy profile switch (POST /auth/profile-switch, /auth/login, owner PIN). */
    suspend fun commitProfileSwitch(original: Session, targetProfileId: String, tokens: AuthTokens) {
        backend.release()
        backend.saveProfile(original, targetProfileId, tokens)
        try { withTimeoutOrNull(ACCESS_WAIT_MS) { backend.reloadAccess() } }
        catch (e: CancellationException) { throw e } catch (_: Exception) { }
    }

    private companion object {
        val REFRESHABLE = setOf("session_stale", "token_expired")
        const val ACCESS_WAIT_MS = 5_000L
    }
}

/** POST /auth/switch refusals, by what the person holding the remote can do about them. */
internal fun mapSwitchFailure(e: ApiException): SwitchResult = when {
    e.status == 429 -> SwitchResult.LockedOut(lockoutSeconds(e.retryAfterSeconds))
    e.error?.code == "pin_required" -> SwitchResult.PinRequired
    e.error?.code == "pin_invalid" -> SwitchResult.PinInvalid
    e.error?.code == "pin_setup_required" -> SwitchResult.PinSetupRequired
    e.error?.code == "not_shared_device" -> SwitchResult.Failed(
        UserError("Not a shared TV", "This TV is signed in as one person's own device, so nobody else can be picked here. Pair it again as a shared TV to switch people.", canRetry = false),
    )
    else -> SwitchResult.Failed(ApiErrors.describe(e))
}

/** Retry-After as sent, kept within a minute's guess (header missing) and an hour. */
internal fun lockoutSeconds(retryAfter: Int?): Int = (retryAfter ?: 60).coerceIn(1, 3600)

private class DefaultSwitchBackend(
    private val api: StreamHubApi,
    private val session: SessionStore,
    private val playback: PlaybackCoordinator,
    private val channels: ChannelListCache,
    private val me: MeRepository,
) : SwitchBackend {
    /** The session the last [post] was sent from: the tokens it returned may only replace that one. */
    private var posted: Session? = null

    override suspend fun switchable(): List<SwitchableUser> = api.switchableUsers().users
    override suspend fun post(userId: String, pin: String?): AuthTokens {
        val from = session.current()
        posted = from
        return api.switchUser(from, userId, pin)
    }
    // Any ordinary authenticated call exchanges a stale token pair; this one is also wanted afterwards.
    override suspend fun refreshSession() { api.me() }
    override suspend fun release() { playback.clearForProfileSwitch(); channels.clear() }
    override suspend fun save(tokens: AuthTokens) {
        session.saveUserSwitchTokens(posted ?: throw CancellationException("Session changed"), tokens)
        posted = null
    }
    override suspend fun saveProfile(original: Session, targetProfileId: String, tokens: AuthTokens) =
        session.saveSwitchedTokens(original, targetProfileId, tokens)
    override suspend fun reloadAccess() { me.clear(); me.refresh() }
}
