package com.streamhub.tv.feature.home

import com.streamhub.tv.core.data.model.LanguageNames
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.model.SportsChannel
import com.streamhub.tv.core.data.model.SportsEvent
import com.streamhub.tv.core.data.model.SportsFeed
import com.streamhub.tv.core.data.model.SportsKinds
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/*
 * What the Sports screen shows, as pure functions of the server's answer and the clock: live games
 * first, then what is coming by day, then game feeds (channels that carry a game but give no time)
 * and the always-on networks. Times are the viewer's own: the server is told the zone, and every
 * time is shown on the local clock.
 */

/** A day of "Coming up", earliest game first. */
internal data class SportsDay(val date: LocalDate, val events: List<SportsEvent>)

internal data class SportsSections(
    val live: List<SportsEvent> = emptyList(),
    val days: List<SportsDay> = emptyList(),
    /** kind=channel: a game on a channel that names no time. Listed without a live or upcoming claim. */
    val gameFeeds: List<SportsEvent> = emptyList(),
) {
    val isEmpty: Boolean get() = live.isEmpty() && days.isEmpty() && gameFeeds.isEmpty()
}

private fun instant(iso: String): Instant? = runCatching { Instant.parse(iso) }.getOrNull()
    ?: runCatching { java.time.OffsetDateTime.parse(iso).toInstant() }.getOrNull()

/** A game feed makes no claim about when it plays: neither live nor upcoming. */
internal fun SportsEvent.isGameFeed(): Boolean = kind == SportsKinds.CHANNEL || status == SportsKinds.UNSCHEDULED

/** On air by its own times; never a game feed, and never an event whose times cannot be read. */
internal fun sportsIsLive(event: SportsEvent, now: Instant): Boolean {
    if (event.isGameFeed()) return false
    val start = instant(event.start) ?: return false
    val stop = instant(event.stop) ?: return false
    return start <= now && now < stop
}

internal fun sportsIsAhead(event: SportsEvent, now: Instant): Boolean =
    !event.isGameFeed() && instant(event.start)?.let { it > now } == true

internal fun sportsDay(event: SportsEvent, zone: ZoneId): LocalDate? = instant(event.start)?.atZone(zone)?.toLocalDate()

/**
 * [date] is the day asked for, [today] the viewer's day. Live games and game feeds belong to today
 * only. Another day's page still overlaps a late game of the evening before: that one stays with
 * its own day and is left out.
 */
internal fun sportsSections(events: List<SportsEvent>, now: Instant, zone: ZoneId, date: LocalDate, today: LocalDate): SportsSections {
    val onToday = date == today
    val ahead = events.filter { sportsIsAhead(it, now) && (sportsDay(it, zone) ?: return@filter false) >= date }
    val days = ahead.groupBy { sportsDay(it, zone)!! }.toSortedMap().map { (day, list) -> SportsDay(day, list.sortedBy { instant(it.start) }) }
    return SportsSections(
        live = if (onToday) events.filter { sportsIsLive(it, now) } else emptyList(),
        days = days,
        gameFeeds = if (onToday) events.filter { it.isGameFeed() } else emptyList(),
    )
}

/** The game as two sides when the server found them ("Away at Home"), else the event's own title. */
internal fun sportsEventTitle(event: SportsEvent): String {
    val home = event.home?.trim().orEmpty()
    val away = event.away?.trim().orEmpty()
    return if (home.isNotEmpty() && away.isNotEmpty()) "$away at $home" else event.title
}

/** LIVE only where the guide says so; a current listing without that is "On now", a repeat "Replay". */
internal fun sportsLiveBadge(event: SportsEvent): String = when {
    event.replay -> "Replay"
    event.liveEvidence -> "LIVE"
    else -> "On now"
}

/** One channel by name and number; several by how many. */
internal fun sportsChannelLine(feeds: List<SportsFeed>): String = when (feeds.size) {
    0 -> "No channel listed"
    1 -> feeds[0].channelName + (feeds[0].channelNumber?.takeIf { it > 0 }?.let { " · Ch $it" } ?: "")
    else -> "${feeds.size} channels"
}

/** League (where the server names one) and channel, the line under a card's title. */
internal fun sportsLeagueLine(event: SportsEvent): String =
    listOfNotNull(event.league?.trim()?.takeIf { it.isNotEmpty() }, sportsChannelLine(event.feeds)).joinToString(" · ")

/** The best quality any of the event's channels advertises, for the card's badge. */
internal fun sportsQuality(event: SportsEvent): String? = event.feeds.firstNotNullOfOrNull { it.quality?.takeIf(String::isNotBlank) }

private val TIME = DateTimeFormatter.ofPattern("h:mm a", Locale.US)
private val DAY = DateTimeFormatter.ofPattern("EEEE, MMM d", Locale.US)

/** A time on the viewer's clock; blank when it cannot be read. */
internal fun sportsTime(iso: String, zone: ZoneId): String = instant(iso)?.let { TIME.format(it.atZone(zone)) }.orEmpty()

internal fun sportsStarted(event: SportsEvent, zone: ZoneId): String = sportsTime(event.start, zone).let { if (it.isEmpty()) "On now" else "Started $it" }

internal fun sportsDayLabel(day: LocalDate, today: LocalDate): String = when (day) {
    today -> "Today"
    today.plusDays(1) -> "Tomorrow"
    else -> DAY.format(day)
}

/** The days the day picker offers: today and the six after it. A day that has passed is never listed. */
internal fun sportsDayChoices(today: LocalDate): List<LocalDate> = (0L..6L).map(today::plusDays)

/** A saved or stale day never lies in the past, nor beyond what the picker offers. */
internal fun sportsDate(saved: String?, today: LocalDate): LocalDate {
    val day = saved?.let { runCatching { LocalDate.parse(it) }.getOrNull() } ?: return today
    return if (day < today || day > today.plusDays(6)) today else day
}

/** Today's page runs into tomorrow, so an evening visit still has something to come. */
internal fun sportsDaysAsked(date: LocalDate, today: LocalDate): Int = if (date == today) 2 else 1

/** The two sides in the order the title names them, for "Record every game for…". */
internal fun sportsSides(event: SportsEvent): List<String> =
    listOfNotNull(event.away, event.home).map(String::trim).filter(String::isNotEmpty).distinct()
        .sortedBy { side -> event.title.indexOf(side, ignoreCase = true).let { if (it < 0) Int.MAX_VALUE else it } }

/**
 * An upcoming game as the programme dialog and the recorder take it: the event on its first channel
 * (lowest number), by the programme id the server gave for that channel. Null when no channel is listed.
 */
internal fun sportsProgramme(event: SportsEvent, recordingState: String? = null): Programme? {
    val feed = event.feeds.firstOrNull() ?: return null
    return Programme(
        id = feed.programmeId, channelId = feed.channelId, start = event.start, stop = event.stop, title = event.title,
        recordingState = recordingState, isSport = true, teams = sportsSides(event),
    )
}

/** What Select does on a game. */
internal sealed interface SportsAction {
    /** Live on one channel (or a game feed with one): play it. */
    data class Play(val channelId: String) : SportsAction
    /** Live on several: ask which. */
    data class ChooseFeed(val event: SportsEvent) : SportsAction
    /** Still to come: its time and, where allowed, the recorder. */
    data class Upcoming(val event: SportsEvent) : SportsAction
    /** Nothing to do (no channel listed, or the game is over). */
    data object None : SportsAction
}

internal fun sportsAction(event: SportsEvent, now: Instant): SportsAction = when {
    sportsIsAhead(event, now) -> SportsAction.Upcoming(event)
    !event.isGameFeed() && !sportsIsLive(event, now) -> SportsAction.None
    event.feeds.size == 1 -> SportsAction.Play(event.feeds[0].channelId)
    event.feeds.size > 1 -> SportsAction.ChooseFeed(event)
    else -> SportsAction.None
}

/** The channel a feed choice plays, while the game is still on and still lists that channel. */
internal fun sportsFeedPlaybackId(event: SportsEvent, channelId: String, now: Instant): String? =
    channelId.takeIf { (event.isGameFeed() || sportsIsLive(event, now)) && event.feeds.any { it.channelId == channelId } }

/** A feed's line in the channel picker: number, quality and a readable language. */
internal fun sportsFeedDetail(feed: SportsFeed, locale: Locale = Locale.getDefault()): String? =
    listOfNotNull(feed.channelNumber?.takeIf { it > 0 }?.let { "Ch $it" }, feed.quality?.takeIf(String::isNotBlank), LanguageNames.orNull(feed.language, locale))
        .joinToString(" · ").takeIf { it.isNotEmpty() }

/** A network's second line: its number, and what is on when the guide really says. Never a placeholder. */
internal fun sportsNetworkLine(channel: SportsChannel): String =
    listOfNotNull(channel.channelNumber.takeIf { it > 0 }?.let { "Ch $it" }, channel.now?.title?.trim()?.takeIf { it.isNotEmpty() }?.let { "Now: $it" }).joinToString(" · ")

/** The language button's text. */
internal fun sportsLanguageLabel(code: String?, locale: Locale = Locale.getDefault()): String = when (code) {
    null -> "My languages"
    "all" -> "All languages"
    else -> LanguageNames.of(code, locale)
}
