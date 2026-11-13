package com.streamhub.tv.feature.onboarding

import android.os.SystemClock
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.normalizeServerUrl
import com.streamhub.tv.core.data.model.SwitchableUser
import com.streamhub.tv.core.data.repo.SessionEnd
import com.streamhub.tv.core.data.repo.SwitchResult
import com.streamhub.tv.core.data.repo.UserSwitcher
import com.streamhub.tv.core.ui.theme.ThemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class WhoState(
    val loading: Boolean = true,
    val users: List<SwitchableUser> = emptyList(),
    val serverUrl: String? = null,
    /** The list could not be read; Retry is offered. */
    val loadError: UserError? = null,
    /**
     * The list could not be read because the account this TV is signed in as is suspended. Nobody
     * can be picked until it is reinstated, so "Pair this TV again" is offered beside Retry.
     */
    val canPairAgain: Boolean = false,
    /** The person whose PIN is being asked for. */
    val pinFor: SwitchableUser? = null,
    val pinError: String? = null,
    /** Bumped after every answer to a PIN, so the pad starts empty again. */
    val pinAttempt: Int = 0,
    /** SystemClock.elapsedRealtime() until which no PIN is accepted; 0 = not locked out. */
    val lockedUntilMs: Long = 0,
    val busy: Boolean = false,
    /** Something to read and acknowledge (a person without a PIN, a refusal). */
    val notice: UserError? = null,
    /**
     * Set when the picker has nothing more to do. Part of the state rather than an event: the answer
     * "there is no picker" can come back from a server on the same network before the screen has
     * started listening, and a missed event would leave the TV on "Loading…" for good.
     */
    val outcome: WhoOutcome? = null,
)

enum class WhoOutcome {
    /** Someone was picked (or kept): carry on into the app. */
    Done,
    /** There is no picker on this server or for this device: carry on as if it had not been asked for. */
    Absent,
}

/** What stands in for a face when there is no picture: a short avatar glyph if one is set, else the initial. */
internal fun personGlyph(user: SwitchableUser): String {
    val avatar = user.avatar?.trim().orEmpty()
    if (avatar.isNotEmpty() && avatar.length <= 4 && !avatar.contains('/') && !avatar.contains('.')) return avatar
    return user.displayName.trim().take(1).uppercase().ifEmpty { "?" }
}

/** A picture address for [user], absolute or on the paired server; null when neither field holds one. */
internal fun personImage(user: SwitchableUser, serverUrl: String?): String? =
    sequenceOf(user.picture, user.avatar).filterNotNull().map(String::trim).firstNotNullOfOrNull { value ->
        when {
            value.startsWith("http://", ignoreCase = true) || value.startsWith("https://", ignoreCase = true) -> value
            value.startsWith("/") && !value.startsWith("//") && !serverUrl.isNullOrBlank() -> normalizeServerUrl(serverUrl) + value
            else -> null
        }
    }

@HiltViewModel
class WhoIsWatchingViewModel internal constructor(
    private val switcher: UserSwitcher,
    private val serverUrl: suspend () -> String?,
    private val applyTheme: suspend (themeId: String) -> Unit,
    /** The clock the lockout countdown runs on; the screen reads the same one. */
    private val nowMs: () -> Long,
) : ViewModel() {
    @Inject constructor(switcher: UserSwitcher, themes: ThemeRepository, sessions: SessionStore) :
        this(switcher, { sessions.current().serverUrl }, { themes.select(it) }, SystemClock::elapsedRealtime)

    private val _state = MutableStateFlow(WhoState())
    val state: StateFlow<WhoState> = _state.asStateFlow()
    private var loadJob: Job? = null

    init { load() }

    fun load() {
        loadJob?.cancel()
        // A list that is already up stays up while it is read again: taking it away would take focus with it.
        _state.update { it.copy(loading = it.users.isEmpty(), loadError = null, canPairAgain = false) }
        loadJob = viewModelScope.launch {
            try {
                val server = serverUrl()
                val users = switcher.switchable()
                // null: a personal device, or a server too old to have the picker. An empty list: nobody is
                // set up to be picked on shared TVs, so there is no question to ask either.
                if (users.isNullOrEmpty()) { _state.update { it.copy(outcome = WhoOutcome.Absent) }; return@launch }
                _state.update { it.copy(loading = false, users = users, serverUrl = server) }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                val suspended = (e as? ApiException)?.error?.code == "user_suspended"
                // A list that could not be read again is not shown as if it were current.
                _state.update { it.copy(loading = false, users = emptyList(), loadError = ApiErrors.describe(e), canPairAgain = suspended) }
            }
        }
    }

    /**
     * @param keepCurrent picking the person already signed in just closes the picker (opened from the
     * rail). On the cold-start picker it does not when they have a PIN: whoever sits down has to
     * prove they are that person, so the switch is made again with the PIN. Without a PIN there is
     * nothing to prove and nothing to switch.
     */
    fun pick(user: SwitchableUser, keepCurrent: Boolean) {
        if (_state.value.busy) return
        if (user.current && (keepCurrent || !user.hasPin)) { _state.update { it.copy(outcome = WhoOutcome.Done) }; return }
        if (user.hasPin) _state.update { it.copy(pinFor = user, pinError = null, pinAttempt = it.pinAttempt + 1) }
        else attempt(user, null)
    }

    fun submitPin(pin: String) {
        val s = _state.value
        val user = s.pinFor ?: return
        if (s.busy || nowMs() < s.lockedUntilMs) return
        attempt(user, pin)
    }

    /** The person a switch without a PIN was last tried for, so "Try again" on its failure tries them again. */
    private var triedWithoutPin: SwitchableUser? = null

    private fun attempt(user: SwitchableUser, pin: String?) {
        if (pin == null) triedWithoutPin = user
        _state.update { it.copy(busy = true, pinError = null) }
        viewModelScope.launch {
            val name = user.displayName.ifBlank { "this person" }
            when (val result = switcher.switchTo(user.id, pin)) {
                is SwitchResult.Ok -> {
                    try { applyTheme(result.profile.themeId) } catch (e: CancellationException) { throw e } catch (_: Exception) { }
                    _state.update { it.copy(busy = false, pinFor = null, outcome = WhoOutcome.Done) }
                }
                SwitchResult.PinRequired -> _state.update {
                    it.copy(busy = false, pinFor = user, pinError = if (pin == null) null else "Enter the PIN for $name.", pinAttempt = it.pinAttempt + 1)
                }
                SwitchResult.PinInvalid -> _state.update {
                    it.copy(busy = false, pinFor = user, pinError = "That PIN isn't right. Try again.", pinAttempt = it.pinAttempt + 1)
                }
                SwitchResult.PinSetupRequired -> _state.update {
                    it.copy(busy = false, pinFor = null, notice = UserError(
                        "${name.replaceFirstChar(Char::uppercase)} needs a PIN first",
                        "This person must set a PIN before they can be picked here. They can set one in the StreamHub web app, under their account.",
                        canRetry = false,
                    ))
                }
                is SwitchResult.LockedOut -> _state.update {
                    it.copy(busy = false, pinFor = user, pinError = null, pinAttempt = it.pinAttempt + 1,
                        lockedUntilMs = nowMs() + result.retryAfterSeconds * 1000L)
                }
                // Gone from the list since it was read: say so, and read the list again behind the notice.
                SwitchResult.NotListed -> {
                    _state.update {
                        it.copy(busy = false, pinFor = null, notice = UserError("No longer here", "${name.replaceFirstChar(Char::uppercase)} can't be picked on this TV any more.", canRetry = false))
                    }
                    load()
                }
                is SwitchResult.SessionEnded -> _state.update {
                    // Signed out: the shell explains and goes back to pairing, so nothing is said twice.
                    // Suspended: the pairing is kept; explained here, then the list says the same.
                    if (result.end == SessionEnd.Suspended) it.copy(busy = false, pinFor = null, notice = ApiErrors.sessionEnded(result.end))
                    else it.copy(busy = false, pinFor = null)
                }
                // With the pad open a failure is shown in it, so a dropped connection can be retried with the same person.
                is SwitchResult.Failed -> _state.update {
                    if (it.pinFor != null && pin != null) it.copy(busy = false, pinError = "${result.error.title}. ${result.error.body}", pinAttempt = it.pinAttempt + 1)
                    else it.copy(busy = false, pinFor = null, notice = result.error)
                }
            }
        }
    }

    fun cancelPin() { if (!_state.value.busy) _state.update { it.copy(pinFor = null, pinError = null) } }
    /**
     * The list is read again behind the acknowledged notice: what was refused may have changed who
     * can be picked, or whether this TV has a picker at all (403 `not_shared_device`).
     */
    fun dismissNotice() {
        if (_state.value.notice == null) return
        _state.update { it.copy(notice = null) }
        load()
    }

    /** "Try again" on a failure that can be retried (the server was away): the same person once more. */
    fun retryNotice() {
        val user = triedWithoutPin
        if (_state.value.busy) return
        _state.update { it.copy(notice = null) }
        if (user != null) attempt(user, null)
    }
    /** The countdown reached zero. */
    fun lockoutOver() = _state.update { if (nowMs() >= it.lockedUntilMs) it.copy(lockedUntilMs = 0) else it }
}
