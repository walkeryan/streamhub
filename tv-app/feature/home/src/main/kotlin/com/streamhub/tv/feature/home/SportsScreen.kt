package com.streamhub.tv.feature.home

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusRestorer
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
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.ProfileFilterStore
import com.streamhub.tv.core.data.repo.CatalogScope
import com.streamhub.tv.core.data.repo.ProgrammeRecorder
import com.streamhub.tv.core.data.repo.RecordingsRepository
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
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import javax.inject.Inject

/** Events are asked for a page at a time; the server's own ceiling. */
internal const val SPORTS_PAGE = 100
private const val SPORTS_NETWORKS = 24

data class SportsState(
    val filters: NativeSportsFilters = NativeSportsFilters(), val result: SportsEventsPage = SportsEventsPage(),
    /** Always-on sports networks (GET /sports/channels). */
    val networks: SportsChannelsPage = SportsChannelsPage(),
    val library: Boolean = false, val recordings: SportsRecordingsPage = SportsRecordingsPage(),
    val recordingsLoading: Boolean = false, val recordingsError: String? = null,
    val languages: List<LanguageCount> = emptyList(), val page: Int = 0,
    val loading: Boolean = true, val ready: Boolean = false, val error: String? = null, val notice: String? = null,
    /** Recording states of games scheduled from this screen, by programme id, for the mark on their cards. */
    val recorded: Map<String, String?> = emptyMap(),
)

@HiltViewModel
class SportsViewModel @Inject constructor(
    private val api: StreamHubApi, private val session: SessionStore, private val prefs: ProfileFilterStore,
    recordings: RecordingsRepository,
) : ViewModel() {
    private val _state = MutableStateFlow(SportsState())
    val state: StateFlow<SportsState> = _state.asStateFlow()
    private var scope: CatalogScope? = null
    private suspend fun ensureScope(expected: CatalogScope) {
        currentCoroutineContext().ensureActive()
        if (scope != expected || session.current().catalogScope() != expected) throw CancellationException("Catalog scope changed")
    }
    private var request: Job? = null
    private var recordingRequest: Job? = null

    /** The Record actions of an upcoming game's dialog; a scheduled game gets its mark at once. */
    val recorder = ProgrammeRecorder(recordings, viewModelScope) { programme, state ->
        _state.update { it.copy(recorded = it.recorded + (programme.id to state)) }
    }

    init {
        viewModelScope.launch {
            session.session.map { it.catalogScope() }.distinctUntilChanged().collectLatest { owner ->
                val id = owner.profileId
                request?.cancel(); recordingRequest?.cancel(); scope = owner; _state.value = SportsState()
                if (id != null) {
                    try {
                        val saved = prefs.read(id, "native_sports")?.let { StreamHubJson.decodeFromJsonElement<NativeSportsFilters>(it) } ?: NativeSportsFilters()
                        ensureScope(owner)
                        // The day is a visit's choice, not a saved one: a stored date would be in the past tomorrow.
                        _state.update { it.copy(filters = saved.copy(date = null, status = "all")) }
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
        // The sport counts stay while the new answer loads: the chip that was pressed must not vanish under focus.
        _state.update { it.copy(recordingsLoading=false, recordingsError=null, filters = value, result = SportsEventsPage(sports = it.result.sports), recordings = SportsRecordingsPage()) }
        val owner = scope ?: return
        val id = owner.profileId ?: return
        viewModelScope.launch {
            try { ensureScope(owner); prefs.write(id, "native_sports", StreamHubJson.encodeToJsonElement(value.copy(date = null, status = "all"))) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { if (scope == owner) _state.update { it.copy(notice = "Filters apply now, but could not be saved.") } }
        }
        refresh(0)
        if(_state.value.library) refreshRecordings(0)
    }
    /** [quiet]: the periodic refresh, which keeps what is shown until the new answer is in. */
    fun refresh(page: Int = _state.value.page, quiet: Boolean = false) {
        if (!_state.value.ready) return
        val owner = scope ?: return
        val f = _state.value.filters
        if (quiet && _state.value.loading) return
        request?.cancel()
        request = viewModelScope.launch {
            _state.update { it.copy(loading = true, page = page, error = if (quiet) it.error else null, result = if (it.page == page) it.result else SportsEventsPage(sports = it.result.sports)) }
            try {
                val zone = ZoneId.systemDefault()
                val today = LocalDate.now(zone)
                val date = sportsDate(f.date, today)
                val result = api.sportsEvents(f, date.toString(), sportsDaysAsked(date, today), zone.id, page * SPORTS_PAGE, SPORTS_PAGE)
                ensureScope(owner)
                _state.update { it.copy(result = result, loading = false, error = null) }
                // The networks are a second, slower-moving list: a failure there leaves the games standing.
                try { val networks = api.sportsChannels(f.language, f.query, SPORTS_NETWORKS); ensureScope(owner); _state.update { it.copy(networks = networks) } }
                catch (e: CancellationException) { throw e }
                catch (_: Exception) { ensureScope(owner) }
                if (!quiet) {
                    try { val languages = api.channelLanguages(); ensureScope(owner); _state.update { it.copy(languages = languages) } }
                    catch (e: CancellationException) { throw e }
                    catch (_: Exception) { ensureScope(owner); _state.update { it.copy(notice = "Language choices could not refresh.") } }
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                ensureScope(owner)
                // A failed background refresh keeps the list that is on screen.
                _state.update { if (quiet && it.result.events.isNotEmpty()) it.copy(loading = false) else it.copy(loading = false, error = e.message ?: "Could not load sports") }
            }
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

/** The card that opened a dialog, so focus returns to it. */
private data class EventChoice(val eventId: String, val opener: FocusRequester)

/**
 * Sports, as the web shows it: a sport filter, the games that are live, what is coming by day on
 * the viewer's own clock, channels carrying a game without a time ("Game feeds") and the always-on
 * networks. One press plays: a live game on one channel starts at once, one on several asks which,
 * and a game still to come opens its programme dialog (its time and, where allowed, the recorder).
 */
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
    // A live game on several channels: which one. An upcoming game: its dialog.
    var feedsFor by remember { mutableStateOf<EventChoice?>(null) }
    var upcoming by remember { mutableStateOf<EventChoice?>(null) }
    val controls = remember { listOf("all", "day", "language", "search").associateWith { FocusRequester() } }
    val firstControl = controls.getValue("all")
    var needsInitialFocus by rememberSaveable { mutableStateOf(true) }
    LaunchedEffect(s.ready) {
        if (!s.ready) { needsInitialFocus = true; picker = null; feedsFor = null; upcoming = null }
        else if (needsInitialFocus) {
            withFrameNanos { }
            if (runCatching { firstControl.requestFocus() }.getOrDefault(false)) needsInitialFocus = false
        }
    }
    LaunchedEffect(s.page, s.filters, s.library, s.recordings.offset) { feedsFor = null; upcoming = null; list.scrollToItem(0) }
    // The clock the sections are cut by, and the list itself, kept current only while this screen is in front.
    var now by remember { mutableStateOf(Instant.now()) }
    WhileVisible(vm) { while (true) { now = Instant.now(); delay(15_000) } }
    WhileVisible(vm) { while (true) { delay(30_000); vm.refresh(quiet = true) } }
    val zone = remember { ZoneId.systemDefault() }
    val today = remember(now) { now.atZone(zone).toLocalDate() }
    val date = sportsDate(s.filters.date, today)
    val sections = remember(s.result.events, now, date) { sportsSections(s.result.events, now, zone, date, today) }
    val recording by vm.recorder.availability.collectAsStateWithLifecycle()
    val record by vm.recorder.state.collectAsStateWithLifecycle()

    fun open(event: SportsEvent, opener: FocusRequester) {
        when (val action = sportsAction(event, Instant.now())) {
            is SportsAction.Play -> launcher.play(action.channelId)
            is SportsAction.ChooseFeed -> feedsFor = EventChoice(event.id, opener)
            is SportsAction.Upcoming -> { vm.recorder.reset(); upcoming = EventChoice(event.id, opener) }
            SportsAction.None -> Unit
        }
    }

    Column(Modifier.fillMaxSize().padding(vertical = Tokens.Canvas.SafeMarginY), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth().padding(start = Tokens.Canvas.SafeMarginX), verticalAlignment = Alignment.CenterVertically) {
            Text("Sports", style = MaterialTheme.typography.headlineMedium, color = t.text, modifier = Modifier.padding(end = 16.dp))
            // Day, language, search and the two other views. The row scrolls with focus and ends at the safe margin.
            LazyRow(Modifier.weight(1f).focusRestorer(), horizontalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = PaddingValues(start = 4.dp, end = Tokens.Canvas.SafeMarginX, top = 4.dp, bottom = 4.dp)) {
                item("day") { ShButton("${sportsDayLabel(date, today)} ▾", { picker = "day" }, Modifier.focusRequester(controls.getValue("day")), enabled = s.ready && !s.library) }
                item("language") { ShButton("${sportsLanguageLabel(s.filters.language)} ▾", { picker = "language" }, Modifier.focusRequester(controls.getValue("language")), enabled = s.ready) }
                item("search") { ShButton(if (s.filters.query.isBlank()) "Search sports" else "Search: ${s.filters.query.take(24)}", { picker = "search" }, Modifier.focusRequester(controls.getValue("search")), enabled = s.ready) }
                item("library") { ShButton(if (s.library) "✓ Library videos" else "Library videos", { vm.library(!s.library) }) }
                item("multiview") { ShButton("Two-pane TV", onMultiview) }
            }
        }
        // The sport filter: All, then each sport the listed days have, with how many.
        val counts = s.result.sports
        LazyRow(Modifier.fillMaxWidth().focusRestorer(), horizontalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = PaddingValues(horizontal = Tokens.Canvas.SafeMarginX, vertical = 4.dp)) {
            item("all") {
                ShChip("All sports" + (if (counts.isNotEmpty()) " · ${counts.sumOf { it.count }}" else ""), s.filters.sport == null,
                    { if (s.filters.sport != null) vm.filters(s.filters.copy(sport = null)) }, Modifier.focusRequester(firstControl).leftOpensRail(rail), enabled = s.ready)
            }
            items(counts, key = { "sport:${it.name}" }) { entry ->
                ShChip("${entry.name} · ${entry.count}", s.filters.sport == entry.name, { if (s.filters.sport != entry.name) vm.filters(s.filters.copy(sport = entry.name)) })
            }
            // A saved sport nothing is listed for today still has its chip, so it can be seen and left.
            s.filters.sport?.takeIf { chosen -> counts.none { it.name == chosen } }?.let { chosen ->
                item("sport-saved") { ShChip("$chosen · 0", true, { vm.filters(s.filters.copy(sport = null)) }) }
            }
        }
        s.notice?.let { Text(it, color = t.textMuted, modifier = Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX)) }
        if (s.library) Column(Modifier.weight(1f).padding(horizontal = Tokens.Canvas.SafeMarginX), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            SportsRecordingsSection(s.recordings, s.recordingsLoading, s.recordingsError,
                onPage = vm::refreshRecordings, onPlay = { recording ->
                    if (s.recordings.items.any { it.titleId == recording.titleId && it.sourceId == recording.sourceId })
                        sportsRecordingPlayback(recording)?.let { (id, source) -> launcher.play(id, source) }
                })
        } else when {
            s.loading && s.result.events.isEmpty() && s.networks.channels.isEmpty() -> LoadingView()
            s.error != null && s.result.events.isEmpty() -> CenterMessage("Couldn't load sports", s.error) { ShButton("Retry", { vm.refresh() }) }
            else -> LazyColumn(state = list, modifier = Modifier.weight(1f).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(bottom = 8.dp)) {
                val quiet = Modifier.padding(horizontal = Tokens.Canvas.SafeMarginX)
                if (date == today) {
                    if (sections.live.isNotEmpty()) item("live") {
                        FocusRow(sections.live, key = { it.id }, title = "Live now") { _, event, modifier ->
                            val opener = remember(event.id) { FocusRequester() }
                            SportsLiveCard(event, zone, modifier.focusRequester(opener)) { open(event, opener) }
                        }
                    } else item("live-none") {
                        Column(quiet) {
                            SectionTitle("Live now")
                            val next = sections.days.firstOrNull()?.events?.firstOrNull()
                            Text("No games are live right now." + (next?.let { " Next: ${sportsEventTitle(it)}, ${sportsDayLabel(sportsDay(it, zone) ?: today, today).lowercase()} at ${sportsTime(it.start, zone)}." } ?: ""),
                                color = t.textMuted, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
                item("ahead-title") {
                    Column(quiet) {
                        SectionTitle("Coming up")
                        if (sections.days.isEmpty()) Text(
                            (if (date == today) "Nothing more is listed for today or tomorrow." else "No games are listed for ${sportsDayLabel(date, today)}.") + (if (s.filters.sport != null) " Try All sports." else ""),
                            color = t.textMuted, style = MaterialTheme.typography.bodyMedium)
                    }
                }
                items(sections.days, key = { "day:${it.date}" }) { day ->
                    FocusRow(day.events, key = { it.id }, title = sportsDayLabel(day.date, today)) { _, event, modifier ->
                        val opener = remember(event.id) { FocusRequester() }
                        SportsUpcomingCard(event, zone, event.feeds.firstOrNull()?.let { s.recorded[it.programmeId] }, modifier.focusRequester(opener)) { open(event, opener) }
                    }
                }
                if (s.result.total > SPORTS_PAGE) item("pages") {
                    Row(quiet, horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        val pages = (s.result.total + SPORTS_PAGE - 1) / SPORTS_PAGE
                        ShButton("Previous", { if (!s.loading && s.page > 0) vm.refresh(s.page - 1) }, Modifier.leftOpensRail(rail), enabled = s.page > 0)
                        Text("Page ${s.page + 1} of $pages", color = t.textMuted, style = MaterialTheme.typography.labelMedium)
                        ShButton("Next", { if (!s.loading && s.page + 1 < pages) vm.refresh(s.page + 1) }, enabled = s.page + 1 < pages)
                    }
                }
                if (s.result.truncated) item("truncated") { Text("This is a partial list. Pick a sport or search to narrow it.", quiet, color = t.textMuted, style = MaterialTheme.typography.labelMedium) }
                // Channels that carry a game but give no time: listed as they are, with no live or upcoming claim.
                if (sections.gameFeeds.isNotEmpty()) item("game-feeds") {
                    FocusRow(sections.gameFeeds, key = { it.id }, title = "Game feeds") { _, event, modifier ->
                        val opener = remember(event.id) { FocusRequester() }
                        SportsGameFeedCard(event, modifier.focusRequester(opener)) { open(event, opener) }
                    }
                }
                if (s.networks.channels.isNotEmpty()) item("networks") {
                    FocusRow(s.networks.channels, key = { it.channelId }, title = "Sports channels") { _, channel, modifier ->
                        ChannelTile(channel.channelName, channel.channelNumber.takeIf { it > 0 }, channel.logo, channel.now, { launcher.play(channel.channelId) }, modifier)
                    }
                }
                if (s.networks.total > s.networks.channels.size && s.networks.channels.isNotEmpty()) item("networks-more") {
                    Text("Showing ${s.networks.channels.size} of ${s.networks.total} sports channels. Search to find another, or browse them in Live TV.", quiet, color = t.textMuted, style = MaterialTheme.typography.labelMedium)
                }
            }
        }
    }
    if (picker == "search") SportsSearchDialog(s.filters.query, onApply = { vm.filters(s.filters.copy(query = it)) }, onDismiss = { picker = null; restoreFocus(controls.getValue("search")) })
    if (picker == "day") ChoiceListDialog(
        title = "Day",
        choices = sportsDayChoices(today).map { ListChoice(it.toString(), sportsDayLabel(it, today)) },
        selectedKey = date.toString(),
        onChoose = { day -> picker = null; restoreFocus(controls.getValue("day")); if (day != date.toString()) vm.filters(s.filters.copy(date = day.takeIf { it != today.toString() })) },
        onDismiss = { picker = null; restoreFocus(controls.getValue("day")) },
    )
    if (picker == "language") ChoiceListDialog(
        title = "Language",
        choices = listOf(ListChoice("", "My languages"), ListChoice("all", "All languages")) +
            s.languages.filter { it.code != "all" }.map { ListChoice(it.code, LanguageNames.of(it.code), it.count.toString()) },
        selectedKey = s.filters.language.orEmpty(),
        onChoose = { code -> picker = null; restoreFocus(controls.getValue("language")); vm.filters(s.filters.copy(language = code.ifEmpty { null })) },
        onDismiss = { picker = null; restoreFocus(controls.getValue("language")) },
    )
    feedsFor?.let { choice ->
        // A game that ended, or left the list, while its channels were being chosen is no longer offered.
        val event = s.result.events.firstOrNull { it.id == choice.eventId }?.takeIf { it.isGameFeed() || sportsIsLive(it, now) }
        if (event == null) LaunchedEffect(choice) { feedsFor = null; restoreFocus(choice.opener) }
        else ChoiceListDialog(
            title = sportsEventTitle(event),
            subtitle = (if (event.isGameFeed()) event.sport else "${event.sport} · ${sportsStarted(event, zone)}") + " · Choose a channel",
            choices = event.feeds.distinctBy { it.channelId }.map { ListChoice(it.channelId, it.channelName, sportsFeedDetail(it)) },
            selectedKey = null,
            onChoose = { channelId ->
                // Focus goes back to the game first, so Back from the player returns to it.
                feedsFor = null; restoreFocus(choice.opener)
                sportsFeedPlaybackId(event, channelId, Instant.now())?.let(launcher::play)
            },
            onDismiss = { feedsFor = null; restoreFocus(choice.opener) },
        )
    }
    upcoming?.let { choice ->
        val event = s.result.events.firstOrNull { it.id == choice.eventId }
        val close = { upcoming = null; vm.recorder.reset(); restoreFocus(choice.opener) }
        val programme = event?.let { e -> sportsProgramme(e, e.feeds.firstOrNull()?.let { s.recorded[it.programmeId] }) }
        if (event == null || programme == null) LaunchedEffect(choice) { close() }
        else {
            val onAir = sportsIsLive(event, now)
            val watch = { close(); if (event.feeds.size == 1) launcher.play(event.feeds[0].channelId) else feedsFor = choice }
            val whenLine = "${sportsDayLabel(sportsDay(event, zone) ?: today, today)} · ${sportsTime(event.start, zone)} · ${sportsLeagueLine(event)}"
            if (recording.canSchedule) ProgrammeDialog(
                programme = programme.copy(title = sportsEventTitle(event)),
                subtitle = whenLine,
                onWatch = if (onAir) watch else null,
                canRecordThis = programme.recordingState != RecordingStates.SCHEDULED && programme.recordingState != RecordingStates.RECORDING,
                busy = record.busy,
                outcome = record.outcome,
                // Scheduled by the programme the server named for this channel, under the title the guide has.
                onRecord = { vm.recorder.recordOnce(programme) },
                onRecordSeries = { vm.recorder.recordSeries(programme) },
                onRecordTeam = vm.recorder::recordTeam,
                onDismiss = close,
            )
            // Nothing to schedule here (no recorder, or no permission): when it is on, and a way back.
            else ErrorDialog(UserError(sportsEventTitle(event), whenLine, canRetry = false), onRetry = {}, onDismiss = close, dismissLabel = "Close")
        }
    }
}
