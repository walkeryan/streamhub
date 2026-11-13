package com.streamhub.tv.feature.onboarding

import android.os.SystemClock
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.normalizeServerUrl
import com.streamhub.tv.core.data.model.SwitchableUser
import com.streamhub.tv.core.data.repo.SwitchResult
import com.streamhub.tv.core.data.repo.UserSwitchRepository
import com.streamhub.tv.core.ui.theme.ThemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
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
)

sealed interface WhoEvent {
    /** Someone was picked (or kept): carry on into the app. */
    data object Done : WhoEvent
    /** There is no picker on this server or for this device: carry on as if it had not been asked for. */
    data object Absent : WhoEvent
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
class WhoIsWatchingViewModel @Inject constructor(
    private val switcher: UserSwitchRepository,
    private val themes: ThemeRepository,
    private val sessions: SessionStore,
) : ViewModel() {
    private val _state = MutableStateFlow(WhoState())
    val state: StateFlow<WhoState> = _state.asStateFlow()
    private val _events = MutableSharedFlow<WhoEvent>(extraBufferCapacity = 1)
    val events: SharedFlow<WhoEvent> = _events
    private var loadJob: Job? = null

    init { load() }

    fun load() {
        loadJob?.cancel()
        _state.update { it.copy(loading = true, loadError = null) }
        loadJob = viewModelScope.launch {
            try {
                val server = sessions.current().serverUrl
                val users = switcher.switchable()
                if (users.isNullOrEmpty()) { _events.emit(WhoEvent.Absent); return@launch }
                _state.update { it.copy(loading = false, users = users, serverUrl = server) }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(loading = false, loadError = ApiErrors.describe(e)) } }
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
        if (user.current && (keepCurrent || !user.hasPin)) { _events.tryEmit(WhoEvent.Done); return }
        if (user.hasPin) _state.update { it.copy(pinFor = user, pinError = null, pinAttempt = it.pinAttempt + 1) }
        else attempt(user, null)
    }

    fun submitPin(pin: String) {
        val s = _state.value
        val user = s.pinFor ?: return
        if (s.busy || SystemClock.elapsedRealtime() < s.lockedUntilMs) return
        attempt(user, pin)
    }

    private fun attempt(user: SwitchableUser, pin: String?) {
        _state.update { it.copy(busy = true, pinError = null) }
        viewModelScope.launch {
            val name = user.displayName.ifBlank { "this person" }
            when (val result = switcher.switchTo(user.id, pin)) {
                is SwitchResult.Ok -> {
                    try { themes.select(result.profile.themeId) } catch (e: CancellationException) { throw e } catch (_: Exception) { }
                    _state.update { it.copy(busy = false, pinFor = null) }
                    _events.emit(WhoEvent.Done)
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
                        lockedUntilMs = SystemClock.elapsedRealtime() + result.retryAfterSeconds * 1000L)
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
    fun dismissNotice() = _state.update { it.copy(notice = null) }
    /** The countdown reached zero. */
    fun lockoutOver() = _state.update { if (SystemClock.elapsedRealtime() >= it.lockedUntilMs) it.copy(lockedUntilMs = 0) else it }
}
