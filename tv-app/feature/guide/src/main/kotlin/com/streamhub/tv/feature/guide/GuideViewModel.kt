package com.streamhub.tv.feature.guide

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.repo.ChannelListCache
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.Instant
import java.time.temporal.ChronoUnit
import javax.inject.Inject

data class GuideProgramme(val p: Programme, val start: Instant, val stop: Instant)

data class GuideState(
    val loading: Boolean = true,
    val error: String? = null,
    val query: String = "",
    val scheduledOnly: Boolean = true,
    val page: Int = 0,
    val total: Int? = null,
    /** Left edge of the grid: 'now' floored to 30 min, minus the catch-up look-back. */
    val windowStart: Instant = floor30(Instant.now()),
    val windowEnd: Instant = floor30(Instant.now()).plus(Duration.ofHours(6)),
    val channels: List<Channel> = emptyList(),
    val programmes: Map<String, List<GuideProgramme>> = emptyMap(),
    val selected: GuideProgramme? = null,
    val selectedChannel: Channel? = null,
)

fun floor30(i: Instant): Instant {
    val m = i.truncatedTo(ChronoUnit.MINUTES)
    val epochMin = m.epochSecond / 60
    return Instant.ofEpochSecond((epochMin - epochMin % 30) * 60)
}

/** EPG grid: 30-min columns, 2 h visible, scrolls horizontally across [windowStart, windowEnd]. */
@HiltViewModel
class GuideViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val cache: ChannelListCache,
) : ViewModel() {
    private val _state = MutableStateFlow(GuideState())
    val state: StateFlow<GuideState> = _state.asStateFlow()

    private var loadJob: Job? = null

    init { load() }

    fun search(value: String) {
        _state.update { it.copy(query = value, page = 0) }
        load(debounce = true)
    }

    fun schedulesOnly(value: Boolean) {
        _state.update { it.copy(scheduledOnly = value, page = 0) }
        load()
    }

    fun page(delta: Int) {
        val s = _state.value
        val next = s.page + delta
        if (next < 0 || (delta > 0 && (s.total?.let { next * PAGE_SIZE >= it } ?: (s.channels.size < PAGE_SIZE)))) return
        _state.update { it.copy(page = next) }
        load()
    }

    fun load(debounce: Boolean = false) {
        loadJob?.cancel()
        _state.update { it.copy(loading = true, error = null) }
        loadJob = viewModelScope.launch {
            if (debounce) delay(300)
            val request = _state.value
            runCatching {
                val list = cache.fetch(q = request.query.trim().ifBlank { null },
                    hasEpg = request.scheduledOnly, limit = PAGE_SIZE, offset = request.page * PAGE_SIZE)
                check(list.channels.size <= PAGE_SIZE) { "Update the hub server to enable paged guide loading." }
                // Grid starts 2 h back so catch-up programmes are reachable by pressing Left.
                val start = floor30(Instant.now()).minus(Duration.ofHours(2))
                val end = start.plus(Duration.ofHours(8))
                val ids = list.channels.map { it.id }
                val epg = if (ids.isEmpty()) emptyMap() else api.epg(ids, start.toString(), end.toString())
                val progs = epg.mapValues { (_, l) ->
                    l.mapNotNull { p ->
                        runCatching { GuideProgramme(p, Instant.parse(p.start), Instant.parse(p.stop)) }.getOrNull()
                    }.filter { it.stop > start && it.start < end }.sortedBy { it.start }
                }
                Triple(list, progs, start to end)
            }.onSuccess { (ch, progs, win) ->
                _state.update { it.copy(loading = false, channels = ch.channels, total = ch.total, programmes = progs, windowStart = win.first, windowEnd = win.second, selected = null, selectedChannel = null) }
            }.onFailure { e ->
                if (e is CancellationException) throw e
                _state.update { it.copy(loading = false, error = e.message) }
            }
        }
    }

    companion object { const val PAGE_SIZE = 40 }

    fun select(ch: Channel, gp: GuideProgramme?) = _state.update { it.copy(selected = gp, selectedChannel = ch) }
}
