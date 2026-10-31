package com.streamhub.tv.feature.guide

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.repo.ChannelListCache
import dagger.hilt.android.lifecycle.HiltViewModel
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

    init { load() }

    fun load() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            runCatching {
                val list = cache.fetch()
                // Grid starts 2 h back so catch-up programmes are reachable by pressing Left.
                val start = floor30(Instant.now()).minus(Duration.ofHours(2))
                val end = start.plus(Duration.ofHours(8))
                val ids = list.channels.map { it.id }
                val epg = ids.chunked(80).flatMap { chunk ->
                    api.epg(chunk, start.toString(), end.toString()).entries.map { it.key to it.value }
                }.toMap()
                val progs = epg.mapValues { (_, l) ->
                    l.mapNotNull { p ->
                        runCatching { GuideProgramme(p, Instant.parse(p.start), Instant.parse(p.stop)) }.getOrNull()
                    }.filter { it.stop > start && it.start < end }.sortedBy { it.start }
                }
                Triple(list.channels, progs, start to end)
            }.onSuccess { (ch, progs, win) ->
                _state.update { it.copy(loading = false, channels = ch, programmes = progs, windowStart = win.first, windowEnd = win.second) }
            }.onFailure { e -> _state.update { it.copy(loading = false, error = e.message) } }
        }
    }

    fun select(ch: Channel, gp: GuideProgramme?) = _state.update { it.copy(selected = gp, selectedChannel = ch) }
}
