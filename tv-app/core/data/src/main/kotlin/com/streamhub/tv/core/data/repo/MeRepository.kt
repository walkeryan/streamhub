package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.Me
import com.streamhub.tv.core.data.model.Permissions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicInteger
import javax.inject.Inject
import javax.inject.Singleton

/** What is known about the signed-in user's access. */
sealed interface MeState {
    /** Not read yet (cold start, or just after a switch). Nothing permission-gated is offered. */
    data object Unknown : MeState

    /**
     * The server has no GET /auth/me (404): it predates users and roles. Everything is offered as it
     * was before, except what only an administrator could do, which follows the profile's admin flag.
     */
    data class Absent(val admin: Boolean) : MeState

    data class Known(val me: Me) : MeState

    fun can(permission: String): Boolean = when (this) {
        is Known -> me.status == "active" && permission in me.permissions
        is Absent -> admin || permission !in Permissions.ADMIN_ONLY
        Unknown -> false
    }

    /** A household TV: "Who's watching?" is shown on every cold start and from the rail. */
    val sharedDevice: Boolean get() = this is Known && me.sharedDevice
}

/**
 * Caches GET /auth/me. Read on start, after pairing, after a user switch and whenever the token
 * pair had to be exchanged (the server answers 401 `session_stale` when a role or permission
 * changed). A failed read keeps what was known: a restarting server must not strip the screen of
 * its actions.
 */
@Singleton
class MeRepository internal constructor(
    private val fetch: suspend () -> Me,
    private val isAdminProfile: suspend () -> Boolean,
    private val saveTokens: suspend (AuthTokens) -> Unit,
    private val rememberSharedDevice: suspend (Boolean) -> Unit,
    refreshSignals: Flow<Unit>,
    scope: CoroutineScope,
) {
    @Inject constructor(api: StreamHubApi, session: SessionStore, prefs: LocalPrefs, events: SessionEvents) : this(
        fetch = { api.me() },
        isAdminProfile = { session.current().profile?.isAdmin == true },
        saveTokens = { session.saveTokens(it) },
        rememberSharedDevice = { prefs.setSharedDevice(it) },
        refreshSignals = events.refreshed,
        scope = CoroutineScope(SupervisorJob() + Dispatchers.Default),
    )

    private val _state = MutableStateFlow<MeState>(MeState.Unknown)
    val state: StateFlow<MeState> = _state.asStateFlow()

    /** Bumped by [clear]: an answer for the previous user is never published for the next one. */
    private val epoch = AtomicInteger(0)

    init {
        scope.launch {
            // One read per burst of refreshes, and a pause after it: a server that kept answering
            // session_stale would otherwise have this loop refresh the tokens forever.
            refreshSignals.conflate().collect {
                try { refresh() } catch (e: CancellationException) { throw e } catch (_: Exception) { }
                delay(SIGNAL_SPACING_MS)
            }
        }
    }

    fun can(permission: String): Boolean = _state.value.can(permission)

    /** Reads /auth/me. Returns the state afterwards, which is the previous one when the read failed. */
    suspend fun refresh(): MeState {
        val started = epoch.get()
        val next = try {
            val me = fetch()
            if (epoch.get() != started) return _state.value
            // A user who was waiting for approval gets their real tokens here, once.
            me.tokens?.let { saveTokens(it) }
            MeState.Known(me.copy(tokens = null))
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException) {
            if (e.status != 404) return _state.value
            MeState.Absent(isAdminProfile())
        } catch (_: Exception) {
            return _state.value
        }
        if (epoch.get() != started) return _state.value
        _state.value = next
        rememberSharedDevice(next.sharedDevice)
        return next
    }

    /** Forget the current user: called when the session changes hands, before the next [refresh]. */
    fun clear() {
        epoch.incrementAndGet()
        _state.value = MeState.Unknown
    }

    private companion object {
        const val SIGNAL_SPACING_MS = 5_000L
    }
}
