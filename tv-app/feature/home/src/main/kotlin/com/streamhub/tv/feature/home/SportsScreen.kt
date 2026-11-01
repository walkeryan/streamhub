package com.streamhub.tv.feature.home

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.ProfileFilterStore
import com.streamhub.tv.core.data.repo.CatalogScope
import com.streamhub.tv.core.data.repo.catalogScope
import com.streamhub.tv.core.ui.components.*
import com.streamhub.tv.core.ui.focus.LocalRailController
import com.streamhub.tv.core.ui.focus.leftOpensRail
import com.streamhub.tv.core.ui.theme.LocalThemeTokens
import com.streamhub.tv.core.ui.theme.Tokens
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import javax.inject.Inject

data class SportsState(
    val filters: NativeSportsFilters = NativeSportsFilters(), val result: SportsEventsPage = SportsEventsPage(),
    val library: Boolean = false, val recordings: SportsRecordingsPage = SportsRecordingsPage(),
    val recordingsLoading: Boolean = false, val recordingsError: String? = null,
    val languages: List<LanguageCount> = emptyList(), val page: Int = 0,
    val loading: Boolean = true, val ready: Boolean = false, val error: String? = null, val notice: String? = null,
)

@HiltViewModel
class SportsViewModel @Inject constructor(private val api: StreamHubApi, private val session: SessionStore, private val prefs: ProfileFilterStore) : ViewModel() {
    private val _state = MutableStateFlow(SportsState())
    val state: StateFlow<SportsState> = _state.asStateFlow()
    private var scope: CatalogScope? = null
    private suspend fun ensureScope(expected: CatalogScope) {
        currentCoroutineContext().ensureActive()
        if (scope != expected || session.current().catalogScope() != expected) throw CancellationException("Catalog scope changed")
    }
    private var request: Job? = null
    private var recordingRequest: Job? = null
    init {
        viewModelScope.launch {
            session.session.map { it.catalogScope() }.distinctUntilChanged().collectLatest { owner ->
                val id = owner.profileId
                request?.cancel(); recordingRequest?.cancel(); scope = owner; _state.value = SportsState()
                if (id != null) {
                    try {
                        val saved = prefs.read(id, "native_sports")?.let { StreamHubJson.decodeFromJsonElement<NativeSportsFilters>(it) } ?: NativeSportsFilters()
                        ensureScope(owner)
                        _state.update { it.copy(filters = saved) }
                    } catch (e: CancellationException) { throw e }
                    catch (_: Exception) { ensureScope(owner); _state.update { it.copy(notice = "Saved filters unavailable. Using defaults.") } }
                    ensureScope(owner)
                    _state.update { it.copy(ready = true) }; refresh(0)
                }
            }
        }
    }
    fun filters(value: NativeSportsFilters) {
        if (!_state.value.ready) return
        recordingRequest?.cancel()
        _state.update { it.copy(recordingsLoading=false, recordingsError=null, filters = value, result = SportsEventsPage(), recordings = SportsRecordingsPage()) }
        val owner = scope ?: return
        val id = owner.profileId ?: return
        viewModelScope.launch {
            try { ensureScope(owner); prefs.write(id, "native_sports", StreamHubJson.encodeToJsonElement(value)) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { if (scope == owner) _state.update { it.copy(notice = "Filters apply now, but could not be saved.") } }
        }
        refresh(0)
        if(_state.value.library) refreshRecordings(0)
    }
    fun refresh(page: Int = _state.value.page) {
        if (!_state.value.ready) return
        val owner = scope ?: return
        val f = _state.value.filters
        request?.cancel()
        request = viewModelScope.launch {
            _state.update { it.copy(loading = true, page = page, error = null, result = if (it.page == page) it.result else SportsEventsPage()) }
            try {
                val result = api.sportsEvents(f, page * 40, 40)
                ensureScope(owner)
                _state.update { it.copy(result = result, loading = false) }
                try { val languages = api.channelLanguages(); ensureScope(owner); _state.update { it.copy(languages = languages) } }
                catch (e: CancellationException) { throw e }
                catch (_: Exception) { ensureScope(owner); _state.update { it.copy(notice = "Language choices could not refresh.") } }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { ensureScope(owner); _state.update { it.copy(loading = false, error = e.message ?: "Could not load sports") } }
        }
    }
    fun library(on: Boolean) {
        _state.update {it.copy(library=on)}
        if(on && _state.value.recordings.items.isEmpty()) refreshRecordings(0)
    }
    fun refreshRecordings(offset: Int = _state.value.recordings.offset) {
        if(!_state.value.ready) return
        val owner=scope ?: return
        val filters=_state.value.filters
        recordingRequest?.cancel()
        recordingRequest=viewModelScope.launch {
            _state.update {it.copy(recordingsLoading=true,recordingsError=null)}
            try {
                val page=api.sportsRecordings(filters,offset,40)
                ensureScope(owner)
                _state.update {it.copy(recordings=page,recordingsLoading=false)}
            } catch(e:CancellationException) {throw e}
            catch(_:Exception) {ensureScope(owner);_state.update {it.copy(recordingsLoading=false,recordingsError="Library recordings could not load. Retry; profile restrictions remain enforced.")}}
        }
    }

}

private val knownSports = listOf("American football", "Basketball", "Baseball", "Hockey", "Soccer", "Football", "Tennis", "Golf", "Motorsport", "Cricket", "Rugby", "Combat sports", "Sports")

private data class EventChoice(val event: SportsEvent, val opener: FocusRequester)

@Composable
fun SportsScreen(onMultiview:()->Unit = {}, vm: SportsViewModel = hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val t = LocalThemeTokens.current
    val rail = LocalRailController.current
    val launcher = LocalPlayLauncher.current
    val list = rememberLazyListState()
    val uiScope = rememberCoroutineScope()
    val restoreFocus: (FocusRequester) -> Unit = { target -> uiScope.launch { withFrameNanos { }; runCatching { target.requestFocus() } } }
    var picker by remember { mutableStateOf<String?>(null) }
    var selected by remember { mutableStateOf<EventChoice?>(null) }
    val controls = remember { listOf("sport", "date", "status", "language", "search").associateWith { FocusRequester() } }
    val firstControl = controls.getValue("sport")
    var needsInitialFocus by rememberSaveable { mutableStateOf(true) }
    LaunchedEffect(s.ready) {
        if (!s.ready) { needsInitialFocus = true; picker = null; selected = null }
        else if (needsInitialFocus) {
            withFrameNanos { }
            if (runCatching { firstControl.requestFocus() }.getOrDefault(false)) needsInitialFocus = false
        }
    }
    LaunchedEffect(s.page, s.filters, s.library, s.recordings.offset) { selected = null; list.scrollToItem(0) }
    LaunchedEffect(s.result.events) {
        selected?.takeIf { choice -> s.result.events.none { it.id == choice.event.id } }?.let { _ ->
            selected = null; restoreFocus(firstControl)
        }
    }
    LaunchedEffect(vm) { while (true) { delay(60_000); vm.refresh() } }
    val today = java.time.LocalDate.now(java.time.ZoneId.of("America/New_York"))
    Column(Modifier.fillMaxSize().padding(horizontal = Tokens.Canvas.SafeMarginX, vertical = Tokens.Canvas.SafeMarginY), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            Text("Sports", style = MaterialTheme.typography.headlineMedium, color = t.text, modifier=Modifier.weight(1f))
            ShButton(if(!s.library) "✓ Guide events" else "Guide events", {vm.library(false)}, Modifier.leftOpensRail(rail))
            ShButton(if(s.library) "✓ Library recordings" else "Library recordings", {vm.library(true)})
            ShButton(if(s.filters.query.isBlank()) "Search sports" else "Search: ${s.filters.query.take(24)}", {picker="search"}, Modifier.focusRequester(controls.getValue("search")),enabled=s.ready)
        }
        Text(if(s.library) "Library videos · Guide date/status do not apply · No implied live-event match" else "Events from your guide · Times in America/New_York · Feed languages are provider hints", color = t.textMuted)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShButton(s.filters.sport ?: "All sports", { picker = "sport" }, Modifier.focusRequester(firstControl).leftOpensRail(rail), enabled = s.ready)
            ShButton(s.filters.date ?: "Today", { picker = "date" }, Modifier.focusRequester(controls.getValue("date")), enabled = s.ready && !s.library)
            ShButton(sportsStatusFilterLabel(s.filters.status), { picker = "status" }, Modifier.focusRequester(controls.getValue("status")), enabled = s.ready && !s.library)
            ShButton(s.languages.find { it.code == s.filters.language }?.name ?: when(s.filters.language) { null -> "My languages"; "all" -> "All languages"; "und" -> "Unknown"; else -> s.filters.language!! }, { picker = "language" }, Modifier.focusRequester(controls.getValue("language")), enabled = s.ready)
        }
        if(!s.library) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ShButton("Two-pane TV", onMultiview)
            ShButton("Reset", { vm.filters(NativeSportsFilters()) }, Modifier.leftOpensRail(rail), enabled = s.ready)
            ShButton(if (s.loading) "Refreshing…" else "Refresh", { if (!s.loading) vm.refresh() }, enabled = s.ready)
            ShButton("Previous", { if (!s.loading && s.page > 0) vm.refresh(s.page - 1) }, enabled = s.ready)
            Text("Page ${s.page + 1} · ${if(s.result.truncated) "at least " else ""}${s.result.total} events", color = t.text)
            ShButton("Next", { if (!s.loading && (s.page + 1) * 40 < s.result.total) vm.refresh(s.page + 1) }, enabled = s.ready)
        }
        if(!s.library) Text("Sports guide coverage: ${s.result.coverage.scheduledChannels} / ${s.result.coverage.eligibleChannels} channels${if(s.result.truncated) " · Results capped; narrow filters" else ""}", color = t.textMuted)
        s.notice?.let { Text(it, color = t.textMuted) }
        if(s.library) SportsRecordingsSection(s.recordings,s.recordingsLoading,s.recordingsError,
            onPage=vm::refreshRecordings, onPlay={ recording ->
                if(s.recordings.items.any { it.titleId==recording.titleId && it.sourceId==recording.sourceId })
                    sportsRecordingPlayback(recording)?.let { (id,source) -> launcher.play(id,source) }
            }) else when {
            s.loading && s.result.events.isEmpty() -> LoadingView()
            s.error != null -> CenterMessage("Couldn't load sports", s.error) { ShButton("Retry", { vm.refresh() }) }
            s.result.events.isEmpty() -> CenterMessage("No matching guide events", "Try another date, sport or language. Missing metadata does not prove no games are scheduled.")
            else -> LazyColumn(state = list, modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(8.dp)) {
                listOf("current", "upcoming", "ended").forEach { status ->
                    val events = s.result.events.filter { it.status == status }
                    if (events.isNotEmpty()) item(status) {
                        FocusRow(events, key = { it.id }, title = sportsStatusFilterLabel(status), contentPadding = PaddingValues(8.dp)) { _, event, modifier ->
                            val opener = remember(event.id) { FocusRequester() }
                            SportsEventCard(event, modifier.focusRequester(opener)) { selected = EventChoice(event, opener) }
                        }
                    }
                }
            }
        }
    }
    if(picker=="search") SportsSearchDialog(s.filters.query, onApply={vm.filters(s.filters.copy(query=it))}, onDismiss={picker=null;restoreFocus(controls.getValue("search"))})
    picker?.takeIf { it!="search" }?.let { field ->
        val options = when(field) {
            "sport" -> listOf(FilterOption(null, "All sports")) + (knownSports + s.result.sports.map { it.name }).distinct().map { name ->
                val count = s.result.sports.firstOrNull { it.name == name }?.count
                FilterOption(name, name + (count?.let { " · $it" } ?: ""))
            }
            "date" -> listOf(FilterOption(null, "Today")) + (-1L..7L).filter { it != 0L }.map { day -> val d = today.plusDays(day); FilterOption(d.toString(), d.toString()) }
            "status" -> listOf("all", "current", "upcoming", "replay").map { FilterOption(it, sportsStatusFilterLabel(it)) }
            else -> listOf(FilterOption(null, "My languages"), FilterOption("all", "All languages")) + s.languages.map { FilterOption(it.code, "${if(it.code == "und") "Unknown" else it.name} · ${it.count}") }
        }
        val value = when(field) { "sport" -> s.filters.sport; "date" -> s.filters.date; "status" -> s.filters.status; else -> s.filters.language }
        CatalogFilterPicker("Choose $field", value, options, { v -> vm.filters(when(field) { "sport" -> s.filters.copy(sport=v); "date" -> s.filters.copy(date=v); "status" -> s.filters.copy(status=v ?: "all"); else -> s.filters.copy(language=v) }) }, { picker=null; controls[field]?.let(restoreFocus) })
    }
    selected?.let { choice ->
        // Drop removed/stale events rather than playing a previously selected filter result.
        val current = s.result.events.firstOrNull { it.id == choice.event.id }
        if (current != null) SportsFeedDialog(current, onDismiss = { selected = null; restoreFocus(choice.opener) }) { feed ->
            if (sportsCanWatch(current, java.time.Instant.now()) && current.feeds.any { it.channelId == feed.channelId && it.programmeId == feed.programmeId }) {
                selected = null; launcher.play(feed.channelId)
            }
        }
    }
}
