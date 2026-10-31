package com.streamhub.tv.feature.onboarding

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.PollResult
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.normalizeServerUrl
import com.streamhub.tv.core.data.model.DeviceCode
import com.streamhub.tv.core.data.repo.LocalPrefs
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import javax.inject.Inject

/** Built-in copy of docs/LEGAL.md v1.0 — shown if the server is unreachable or before pairing. */
const val DISCLAIMER_VERSION = "1.0"
val DISCLAIMER_TEXT = """
StreamHub is a media player and organizer. It does not provide, host, sell, promote or distribute any channels, playlists, subscriptions, EPG data or streams.

You may only add sources you are legally authorized to access. You are solely responsible for the sources you configure and for complying with the laws of your jurisdiction and the rights of content owners. The authors and contributors of StreamHub accept no liability for how you use this software.

Streaming-service names and logos are trademarks of their respective owners. StreamHub is not affiliated with, endorsed by or certified by any of them, including Plex, Jellyfin, Netflix, Hulu, Disney, Amazon, Warner Bros. Discovery, Apple, NBCUniversal or Paramount.

This product uses the TMDB API but is not endorsed or certified by TMDB. Availability ("where to watch") data is provided by JustWatch.
""".trimIndent()

enum class OnboardingStep { Server, Disclaimer, Pairing, Done }

data class OnboardingState(
    val step: OnboardingStep = OnboardingStep.Server,
    val serverInput: String = "",
    val checking: Boolean = false,
    val error: String? = null,
    val disclaimerVersion: String = DISCLAIMER_VERSION,
    val disclaimerText: String = DISCLAIMER_TEXT,
    val deviceCode: DeviceCode? = null,
    val pairUrl: String? = null,
)

@HiltViewModel
class OnboardingViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val session: SessionStore,
    private val prefs: LocalPrefs,
) : ViewModel() {
    private val _state = MutableStateFlow(OnboardingState())
    val state: StateFlow<OnboardingState> = _state.asStateFlow()
    private var pollJob: Job? = null

    init {
        viewModelScope.launch {
            val s = session.current()
            val accepted = prefs.disclaimerAcceptedVersion.first()
            _state.update {
                it.copy(
                    serverInput = s.serverUrl.orEmpty(),
                    step = when {
                        s.isPaired && accepted == DISCLAIMER_VERSION -> OnboardingStep.Done
                        s.serverUrl.isNullOrBlank() -> OnboardingStep.Server
                        accepted != DISCLAIMER_VERSION -> OnboardingStep.Disclaimer
                        else -> OnboardingStep.Pairing
                    },
                )
            }
            if (_state.value.step == OnboardingStep.Disclaimer) loadDisclaimer()
            if (_state.value.step == OnboardingStep.Pairing) startPairing()
        }
    }

    fun onServerInput(v: String) = _state.update { it.copy(serverInput = v, error = null) }

    fun submitServer() {
        val raw = _state.value.serverInput.trim()
        if (raw.isEmpty()) { _state.update { it.copy(error = "Enter your StreamHub address, e.g. 192.168.1.10:8787") }; return }
        val base = normalizeServerUrl(raw)
        _state.update { it.copy(checking = true, error = null) }
        viewModelScope.launch {
            val ok = api.health(base)
            if (!ok) {
                _state.update { it.copy(checking = false, error = "Couldn't reach $base. Check the address and that the server is running.") }
                return@launch
            }
            session.setServerUrl(base)
            _state.update { it.copy(checking = false, serverInput = base) }
            val accepted = prefs.disclaimerAcceptedVersion.first()
            loadDisclaimer()
            if (accepted == _state.value.disclaimerVersion) goPairing() else _state.update { it.copy(step = OnboardingStep.Disclaimer) }
        }
    }

    private suspend fun loadDisclaimer() {
        runCatching { api.disclaimer() }.onSuccess { d ->
            _state.update { it.copy(disclaimerVersion = d.version, disclaimerText = d.text.ifBlank { DISCLAIMER_TEXT }) }
        }
    }

    fun acceptDisclaimer() {
        viewModelScope.launch {
            prefs.setDisclaimerAccepted(_state.value.disclaimerVersion)
            // Server-side acceptance needs a profile token; it is posted after pairing (see finishPairing).
            goPairing()
        }
    }

    private fun goPairing() {
        _state.update { it.copy(step = OnboardingStep.Pairing) }
        startPairing()
    }

    fun startPairing() {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            val code = runCatching { api.startDevicePairing() }.getOrElse { e ->
                _state.update { it.copy(error = "Pairing failed: ${e.message}") }; return@launch
            }
            val base = session.current().serverUrl.orEmpty()
            _state.update { it.copy(deviceCode = code, pairUrl = "$base/pair?code=${code.userCode}", error = null) }
            val deadline = System.currentTimeMillis() + code.expiresIn * 1000L
            while (isActive && System.currentTimeMillis() < deadline) {
                delay(code.interval.coerceAtLeast(2) * 1000L)
                when (val r = runCatching { api.pollDevicePairing(code.deviceCode) }.getOrNull()) {
                    is PollResult.Done -> { finishPairing(r.value); return@launch }
                    else -> Unit // pending or transient error: keep polling
                }
            }
            if (isActive) startPairing() // code expired → fetch a fresh one
        }
    }

    private suspend fun finishPairing(tokens: com.streamhub.tv.core.data.model.AuthTokens) {
        session.saveTokens(tokens)
        val v = _state.value.disclaimerVersion
        if (tokens.profile.disclaimerAcceptedVersion != v) runCatching { api.acceptDisclaimer(v) }
        _state.update { it.copy(step = OnboardingStep.Done) }
    }

    fun backToServer() {
        pollJob?.cancel()
        _state.update { it.copy(step = OnboardingStep.Server, deviceCode = null) }
    }
}
