package com.streamhub.tv.feature.search

import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.SearchProgramme
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.data.repo.RequestStatusModel
import com.streamhub.tv.core.data.repo.requestStatus
import com.streamhub.tv.core.data.repo.tileRequest
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

/** What a title's poster carries in a search. */
internal sealed interface TitleMark {
    /** A source of the hub plays it: a small tick. */
    data object PlaysHere : TitleMark
    /** It does not play here and a request for it is in progress: the chip of its phase. */
    data class Requested(val status: RequestStatusModel) : TitleMark
    /** It does not play here and nothing is in progress: the poster carries nothing. */
    data object None : TitleMark
}

internal fun titleMark(tile: Tile): TitleMark = when {
    tile.kind == MediaKind.CHANNEL -> TitleMark.None
    tile.playableInApp -> TitleMark.PlaysHere
    else -> tileRequest(tile)?.let { TitleMark.Requested(requestStatus(it)) } ?: TitleMark.None
}

/** The tick, as the poster's badge. A request draws its own chip (and bar) on the poster; nothing else is said. */
internal fun titleTick(tile: Tile): String? = if (titleMark(tile) == TitleMark.PlaysHere) "✓" else null

private fun instant(iso: String): Instant? = runCatching { Instant.parse(iso) }.getOrNull()
    ?: runCatching { java.time.OffsetDateTime.parse(iso).toInstant() }.getOrNull()

/** On air by its own times; the server's `on_now` when they cannot be read. */
internal fun broadcastOnNow(p: SearchProgramme, now: Instant): Boolean {
    val start = instant(p.start) ?: return p.onNow
    val stop = instant(p.stop) ?: return p.onNow
    return start <= now && now < stop
}

private val TIME = DateTimeFormatter.ofPattern("h:mm a", Locale.US)
private val WEEKDAY = DateTimeFormatter.ofPattern("EEE", Locale.US)

/** "Today 8:30 PM", "Tomorrow 9:00 AM", "Sat 7:00 PM", on the viewer's own clock; blank when unreadable. */
internal fun broadcastStart(iso: String, now: Instant, zone: ZoneId): String {
    val start = instant(iso)?.atZone(zone) ?: return ""
    val day = when (ChronoUnit.DAYS.between(now.atZone(zone).toLocalDate(), start.toLocalDate())) {
        0L -> "Today"
        1L -> "Tomorrow"
        else -> WEEKDAY.format(start)
    }
    return "$day ${TIME.format(start)}"
}

/** Where a broadcast is on: its first channel, and how many more carry it. */
internal fun broadcastChannelLine(p: SearchProgramme): String {
    val others = p.feeds.map { it.channelId }.distinct().count { it != p.channelId }
    return listOfNotNull(p.channelNumber.takeIf { it > 0 }?.toString(), p.channelName.takeIf { it.isNotBlank() }).joinToString(" · ") + (if (others > 0) " +$others more" else "")
}
