package com.streamhub.tv.feature.sources

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.PollResult
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.handoff.DeepLinkLauncher
import com.streamhub.tv.core.data.handoff.HandoffResult
import com.streamhub.tv.core.data.model.PlexPin
import com.streamhub.tv.core.data.model.ServiceRegistryEntry
import com.streamhub.tv.core.data.model.Source
import com.streamhub.tv.core.data.model.SourceCreate
import com.streamhub.tv.core.data.model.SourceType
import com.streamhub.tv.core.data.repo.LocalPrefs
import com.streamhub.tv.core.ui.components.DISCLAIMER_VERSION
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
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import javax.inject.Inject

/** Fallback when /services/registry is unavailable: docs/DEEPLINKS.md seed data. */
val SEED_SERVICES = listOf(
    ServiceRegistryEntry("netflix", "Netflix", listOf(8), "com.netflix.ninja", "https://www.netflix.com/title/{netflix_id}?source=30", null, "https://www.netflix.com/title/{netflix_id}", "true"),
    ServiceRegistryEntry("hulu", "Hulu", listOf(15), "com.hulu.livingroomplus", "https://www.hulu.com/watch/{hulu_id}", ".WKFactivity", "https://www.hulu.com/watch/{hulu_id}", "partial"),
    ServiceRegistryEntry("disney", "Disney+", listOf(337), "com.disney.disneyplus", "https://www.disneyplus.com/browse/entity-{id}", null, "https://www.disneyplus.com/browse/entity-{id}", "unverified"),
    ServiceRegistryEntry("prime", "Prime Video", listOf(9, 119), "com.amazon.amazonvideo.livingroom", "https://app.primevideo.com", null, "https://www.primevideo.com/detail/{asin}", "false"),
    ServiceRegistryEntry("max", "HBO Max", listOf(1899), "com.wbd.stream", "https://play.max.com/video/watch/{id}", null, "https://play.max.com/video/watch/{id}", "unverified"),
    ServiceRegistryEntry("appletv", "Apple TV+", listOf(350), "com.apple.atve.androidtv.appletv", null, null, "https://tv.apple.com", "false"),
    ServiceRegistryEntry("peacock", "Peacock", listOf(386, 387), "com.peacocktv.peacockandroid", null, null, "https://www.peacocktv.com/watch/playback/vod/{id}", "false"),
    ServiceRegistryEntry("paramount", "Paramount+", listOf(531), "com.cbs.ott", null, null, "https://www.paramountplus.com", "false"),
    ServiceRegistryEntry("youtube", "YouTube", listOf(192), "com.google.android.youtube.tv", "https://www.youtube.com/watch?v={id}", null, "https://www.youtube.com/watch?v={id}", "true"),
    ServiceRegistryEntry("tubi", "Tubi", listOf(73), "com.tubitv", null, null, "https://tubitv.com", "false"),
    ServiceRegistryEntry("pluto", "Pluto TV", listOf(300), "tv.pluto.android", null, null, "https://pluto.tv", "false"),
)

sealed interface AddFlow {
    data object None : AddFlow
    data object Disclaimer : AddFlow
    data class Plex(val pin: PlexPin?, val error: String? = null) : AddFlow
    data class Form(val type: SourceType) : AddFlow
    data object Services : AddFlow
}

data class SourcesState(
    val loading: Boolean = true,
    val sources: List<Source> = emptyList(),
    val registry: List<ServiceRegistryEntry> = SEED_SERVICES,
    val flow: AddFlow = AddFlow.None,
    val pendingType: SourceType? = null,
    val busy: Boolean = false,
    val message: String? = null,
    val qr: HandoffResult.ShowQr? = null,
)

@HiltViewModel
class SourcesViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val prefs: LocalPrefs,
    private val session: SessionStore,
    private val launcher: DeepLinkLauncher,
) : ViewModel() {
    private val _state = MutableStateFlow(SourcesState())
    val state: StateFlow<SourcesState> = _state.asStateFlow()
    private var plexJob: Job? = null

    init { refresh() }

    fun refresh() = viewModelScope.launch {
        runCatching { api.sources() }.onSuccess { l -> _state.update { it.copy(loading = false, sources = l.sortedBy { s -> s.priority }) } }
            .onFailure { e -> _state.update { it.copy(loading = false, message = e.message) } }
        runCatching { api.serviceRegistry() }.onSuccess { r -> if (r.isNotEmpty()) _state.update { it.copy(registry = r) } }
    }

    private suspend fun disclaimerAccepted(): Boolean {
        val profileVersion = session.current().profile?.disclaimerAcceptedVersion
        return profileVersion == DISCLAIMER_VERSION || prefs.disclaimerAcceptedVersion.first() == DISCLAIMER_VERSION
    }

    fun startAdd(type: SourceType) = viewModelScope.launch {
        if (type.requiresDisclaimer && !disclaimerAccepted()) {
            _state.update { it.copy(flow = AddFlow.Disclaimer, pendingType = type) }
            return@launch
        }
        when (type) {
            SourceType.PLEX -> startPlex()
            SourceType.STREAMING_SERVICE -> _state.update { it.copy(flow = AddFlow.Services) }
            else -> _state.update { it.copy(flow = AddFlow.Form(type)) }
        }
    }

    fun acceptDisclaimer() = viewModelScope.launch {
        prefs.setDisclaimerAccepted(DISCLAIMER_VERSION)
        runCatching { api.acceptDisclaimer(DISCLAIMER_VERSION) }
        session.current().profile?.let { session.updateProfile(it.copy(disclaimerAcceptedVersion = DISCLAIMER_VERSION)) }
        val t = _state.value.pendingType
        _state.update { it.copy(flow = AddFlow.None, pendingType = null) }
        if (t != null) startAdd(t)
    }

    fun cancelFlow() {
        plexJob?.cancel()
        _state.update { it.copy(flow = AddFlow.None, pendingType = null, busy = false) }
    }

    private fun startPlex() {
        _state.update { it.copy(flow = AddFlow.Plex(null)) }
        plexJob?.cancel()
        plexJob = viewModelScope.launch {
            val pin = runCatching { api.startPlexPin() }.getOrElse { e -> _state.update { it.copy(flow = AddFlow.Plex(null, e.message)) }; return@launch }
            _state.update { it.copy(flow = AddFlow.Plex(pin)) }
            val deadline = System.currentTimeMillis() + 10 * 60_000
            while (isActive && System.currentTimeMillis() < deadline) {
                delay(2_000)
                when (val r = runCatching { api.pollPlexPin(pin.pinId) }.getOrNull()) {
                    is PollResult.Done -> {
                        _state.update { it.copy(flow = AddFlow.None, message = "Linked ${r.value.size} Plex server(s)") }
                        refresh(); return@launch
                    }
                    else -> Unit
                }
            }
        }
    }

    fun submit(type: SourceType, name: String, fields: Map<String, String>) = viewModelScope.launch {
        _state.update { it.copy(busy = true, message = null) }
        val config = JsonObject(fields.filterValues { it.isNotBlank() }.mapValues { (k, v) ->
            if (k == "refresh_hours") JsonPrimitive(v.toIntOrNull() ?: 24) else JsonPrimitive(v.trim())
        })
        runCatching { api.createSource(SourceCreate(type = type, name = name.ifBlank { type.name.lowercase() }, config = config)) }
            .onSuccess { src ->
                _state.update { it.copy(busy = false, flow = AddFlow.None, message = "Added ${src.name}. Syncing…") }
                runCatching { api.syncSource(src.id) }
                refresh()
            }
            .onFailure { e ->
                if (e is ApiException && e.status == 412) {
                    _state.update { it.copy(busy = false, flow = AddFlow.Disclaimer, pendingType = type) }
                } else {
                    _state.update { it.copy(busy = false, message = "Couldn't add source: ${e.message}") }
                }
            }
    }

    /** Toggle a streaming service: creates/deletes a `streaming_service` source with that service_key. */
    fun toggleService(entry: ServiceRegistryEntry) = viewModelScope.launch {
        val existing = _state.value.sources.firstOrNull { it.type == SourceType.STREAMING_SERVICE && it.serviceKey == entry.key }
        runCatching {
            if (existing != null) api.deleteSource(existing.id)
            else api.createSource(SourceCreate(SourceType.STREAMING_SERVICE, entry.name, serviceKey = entry.key, config = JsonObject(emptyMap())))
        }.onFailure { e -> _state.update { it.copy(message = e.message) } }
        refresh()
    }

    fun testLink(entry: ServiceRegistryEntry) {
        when (val r = launcher.test(entry)) {
            is HandoffResult.ShowQr -> _state.update { it.copy(qr = r) }
            is HandoffResult.OpenedApp -> _state.update { it.copy(message = "${entry.name}: opened app (no title link)") }
            is HandoffResult.OpenedTitle -> _state.update { it.copy(message = "${entry.name}: title link OK") }
        }
    }

    fun dismissQr() = _state.update { it.copy(qr = null) }
    fun isInstalled(pkg: String?) = pkg != null && launcher.isInstalled(pkg)

    fun sync(s: Source) = viewModelScope.launch {
        runCatching { api.syncSource(s.id) }.onSuccess { _state.update { it.copy(message = "Sync started for ${s.name}") } }
    }

    fun test(s: Source) = viewModelScope.launch {
        runCatching { api.testSource(s.id) }
            .onSuccess { r -> _state.update { it.copy(message = "${s.name}: ${if (r.ok) "OK" else "Failed"} ${r.message}".trim()) } }
            .onFailure { e -> _state.update { it.copy(message = e.message) } }
    }

    fun delete(s: Source) = viewModelScope.launch {
        runCatching { api.deleteSource(s.id) }.onSuccess { refresh() }
    }
}
