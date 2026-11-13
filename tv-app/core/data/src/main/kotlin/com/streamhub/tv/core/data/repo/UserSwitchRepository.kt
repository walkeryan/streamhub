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
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
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
    /** 404: this person can no longer be picked on this TV. The list has to be read again. */
    data object NotListed : SwitchResult
    /**
     * The session the switch was asked from no longer stands. [SessionEnd.Revoked]: the tokens are
     * gone and the shell has been told to go back to pairing. [SessionEnd.Suspended]: the pairing is
     * kept; nobody can be picked until the account is reinstated or the TV is paired again.
     */
    data class SessionEnded(val end: SessionEnd) : SwitchResult
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
    /** The session [post] was sent from is over: drops its tokens when [end] says they are dead, and tells the shell. */
    suspend fun sessionEnded(end: SessionEnd)
}

/** "Who's watching?" as the picker screen uses it; an interface so its view model is tested against a fake. */
interface UserSwitcher {
    /**
     * The people who can be picked here, or null when there is no picker: a personal device
     * (403 `not_shared_device`) or a server too old to have the route (404). Other failures are thrown.
     */
    suspend fun switchable(): List<SwitchableUser>?
    suspend fun switchTo(userId: String, pin: String?): SwitchResult
}

/**
 * "Who's watching?": the people who may be picked on this shared TV, and becoming one of them.
 * Also the one place a switch is committed (tokens saved, session generation bumped, per-user
 * caches cleared), which the older per-profile switch in Settings goes through as well.
 */
@Singleton
class UserSwitchRepository internal constructor(private val backend: SwitchBackend) : UserSwitcher {
    @Inject constructor(api: StreamHubApi, session: SessionStore, playback: PlaybackCoordinator, channels: ChannelListCache, me: MeRepository, events: SessionEvents) :
        this(DefaultSwitchBackend(api, session, playback, channels, me, events))

    private val mutex = Mutex()

    override suspend fun switchable(): List<SwitchableUser>? =
        try { backend.switchable() }
        catch (e: CancellationException) { throw e }
        catch (e: ApiException) { if (isMissingRoute(e) || e.error?.code == "not_shared_device") null else throw e }

    override suspend fun switchTo(userId: String, pin: String?): SwitchResult = mutex.withLock {
        val tokens = try { post(userId, pin) }
        catch (e: CancellationException) { throw e }
        catch (e: ApiException) {
            val result = mapSwitchFailure(e)
            if (result is SwitchResult.SessionEnded) {
                try { backend.sessionEnded(result.end) } catch (c: CancellationException) { throw c } catch (_: Exception) { }
            }
            return@withLock result
        }
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

    /**
     * POST /auth/switch with the two repeats the contract asks for, each made once at most: after
     * 401 `token_expired` / `session_stale` (which are about this TV's own session and are answered
     * before any PIN is looked at) the session is refreshed first; 409 `switch_conflict` means the
     * device or the person changed in the same moment and is simply sent again. A 401 about the PIN
     * is never repeated: the same wrong PIN twice would cost two of the five guesses.
     */
    private suspend fun post(userId: String, pin: String?): AuthTokens {
        var refreshed = false
        var conflicted = false
        while (true) {
            try { return backend.post(userId, pin) }
            catch (e: ApiException) {
                when {
                    e.status == 401 && e.error?.code in REFRESHABLE && !refreshed -> { refreshed = true; backend.refreshSession() }
                    e.status == 409 && e.error?.code == "switch_conflict" && !conflicted -> conflicted = true
                    else -> throw e
                }
            }
        }
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

/**
 * The one "server too old" answer: a 404 that says "no such route" rather than "no such person".
 * The server's own not-found errors carry a code of their own, the framework's answer for an
 * unknown path does not.
 */
internal fun isMissingRoute(e: ApiException): Boolean = e.status == 404 && e.error?.code.orEmpty() in setOf("", "http_error")

/** POST /auth/switch refusals, by what the person holding the remote can do about them. */
internal fun mapSwitchFailure(e: ApiException): SwitchResult {
    val code = e.error?.code
    return when {
        // Five wrong PINs for this person in 15 minutes; Retry-After is whole seconds.
        e.status == 429 -> SwitchResult.LockedOut(lockoutSeconds(e.retryAfterSeconds))
        e.status == 404 -> SwitchResult.NotListed
        code == "pin_required" -> SwitchResult.PinRequired
        code == "pin_invalid" -> SwitchResult.PinInvalid
        code == "pin_setup_required" -> SwitchResult.PinSetupRequired
        e.status == 401 && SessionEnd.ofCode(code) != null -> SwitchResult.SessionEnded(SessionEnd.ofCode(code)!!)
        code == "not_shared_device" -> SwitchResult.Failed(
            UserError("Not a shared TV", "This TV is signed in as one person's own device, so nobody else can be picked here. Pair it again as a shared TV to switch people.", canRetry = false),
        )
        // Still in conflict after the one repeat: the viewer can press again.
        code == "switch_conflict" -> SwitchResult.Failed(
            UserError("Couldn't switch just now", "This TV or that person's account changed at the same moment. Try again.", canRetry = true),
        )
        else -> SwitchResult.Failed(ApiErrors.describe(e))
    }
}

/** Retry-After as sent, kept within a minute's guess (header missing) and an hour. */
internal fun lockoutSeconds(retryAfter: Int?): Int = (retryAfter ?: 60).coerceIn(1, 3600)

private class DefaultSwitchBackend(
    private val api: StreamHubApi,
    private val session: SessionStore,
    private val playback: PlaybackCoordinator,
    private val channels: ChannelListCache,
    private val me: MeRepository,
    private val events: SessionEvents,
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
    override suspend fun sessionEnded(end: SessionEnd) {
        // The switch call keeps the usual 401 handling out, so what that handling does for every other
        // call is done here: dead tokens go (the server address stays), and the shell hears why.
        val from = posted
        posted = null
        if (end != SessionEnd.Suspended && (from == null || !session.clearExpiredSession(from))) return
        events.emitEnded(end)
    }
}

@Module
@InstallIn(SingletonComponent::class)
abstract class UserSwitchModule {
    @Binds abstract fun switcher(impl: UserSwitchRepository): UserSwitcher
}
