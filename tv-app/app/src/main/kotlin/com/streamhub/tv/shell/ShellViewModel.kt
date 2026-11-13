package com.streamhub.tv.shell

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.auth.AccessTokenSource
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.ShellEntry
import com.streamhub.tv.core.data.auth.shellEntry
import com.streamhub.tv.core.data.repo.PlaybackCoordinator
import com.streamhub.tv.core.data.repo.RoomRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/** What fills the screen. [Web] is the web app; every other one is native and covers it. */
enum class ShellScreen { Loading, Pairing, Web, Unreachable, Settings }

/** What the shell shows, from the stored session and what happened in this run. Pure, so it is unit-tested. */
fun shellScreen(session: Session?, changingServer: Boolean, pageFailed: Boolean, settingsOpen: Boolean): ShellScreen = when {
    session == null -> ShellScreen.Loading
    changingServer || shellEntry(session) == ShellEntry.Pair -> ShellScreen.Pairing
    settingsOpen -> ShellScreen.Settings
    pageFailed -> ShellScreen.Unreachable
    else -> ShellScreen.Web
}

@HiltViewModel
class ShellViewModel @Inject constructor(
    private val sessions: SessionStore,
    private val tokens: AccessTokenSource,
    val coordinator: PlaybackCoordinator,
    private val rooms: RoomRepository,
) : ViewModel() {
    /** null until DataStore has loaded. */
    val session: StateFlow<Session?> = sessions.session.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    private val changingServer = MutableStateFlow(false)
    private val pageFailed = MutableStateFlow(false)
    private val settingsOpen = MutableStateFlow(false)

    val screen: StateFlow<ShellScreen> = combine(session, changingServer, pageFailed, settingsOpen, ::shellScreen)
        .stateIn(viewModelScope, SharingStarted.Eagerly, ShellScreen.Loading)

    /** Why the web app signed out, said over the pairing screen; null once acknowledged. */
    val signedOutNotice = MutableStateFlow<String?>(null)

    /** Each visit to pairing gets a fresh onboarding (a new view-model key). */
    var pairingRun = 0
        private set
    /** Pairing opened from "Change server": it starts at the address rather than at a code. */
    var pairingAtServer = false
        private set

    fun pageFailed() { pageFailed.value = true }
    fun retry() { pageFailed.value = false; settingsOpen.value = false }
    fun openSettings() { settingsOpen.value = true }
    fun closeSettings() { settingsOpen.value = false }

    fun changeServer() {
        pairingRun++; pairingAtServer = true
        settingsOpen.value = false; pageFailed.value = false; changingServer.value = true
    }

    /** Onboarding finished: the TV holds a new pair, which the shell writes into the web app next. */
    fun paired() { changingServer.value = false; pageFailed.value = false; pairingAtServer = false }

    /** The web app said it has no session any more (signed out, revoked, never seeded): back to pairing. */
    fun signedOut(reason: String?) {
        viewModelScope.launch {
            coordinator.clearForProfileSwitch()
            sessions.clearTokens()
            pairingRun++; pairingAtServer = false
            settingsOpen.value = false; pageFailed.value = false
            signedOutNotice.value = reason
        }
    }

    /** "Sign out of this TV" in the native settings: the same, without a reason to show. */
    fun signOutHere() = signedOut(null)

    /** The pair [expected] held is in the web app's storage now: the TV lets go of its refresh token. */
    suspend fun handOver(expected: Session): Boolean = sessions.handOverToWeb(expected)

    /**
     * Play pressed in the web app. The web app's current token (and person) is taken first, so the
     * player's checks start from the session the web app is in; then the TV resolves for itself.
     */
    fun play(play: ShellPlay) {
        viewModelScope.launch {
            try {
                tokens.currentAccessToken(null) ?: return@launch // no session in the web app: it signs out itself
                when (play) {
                    is ShellPlay.Title -> coordinator.playFromShell(play.request, play.startPositionMs)
                    is ShellPlay.Room -> coordinator.open(rooms.joinHeld(play.code, play.memberToken, play.allowUnverifiedAudio))
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { coordinator.reportError(ApiErrors.describe(e)) }
        }
    }
}
