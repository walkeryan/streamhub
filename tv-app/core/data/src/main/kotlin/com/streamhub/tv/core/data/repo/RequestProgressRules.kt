package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.MediaRequest
import com.streamhub.tv.core.data.model.RequestPhases
import com.streamhub.tv.core.data.model.RequestProgress
import com.streamhub.tv.core.data.model.RequestStates
import com.streamhub.tv.core.data.model.Tile
import com.streamhub.tv.core.data.model.TileRequestProgress
import java.time.Instant

/*
 * How a request's progress is said, shared by posters, the title page and the Requests screen (pure,
 * unit-tested). One status per phase: what the request manager last reported, never ahead of it.
 */

/** What a server that sends no progress still tells: only what the request's state shows. */
fun phaseOfState(state: String?): String? = when (state) {
    null, "" -> null
    RequestStates.PENDING_APPROVAL -> RequestPhases.REQUESTED
    RequestStates.SENT -> RequestPhases.SEARCHING
    else -> state
}

/** The chip's words for a phase. A phase this build does not know is shown by its own name. */
fun requestPhaseLabel(phase: String?): String? = when (phase) {
    null, "" -> null
    RequestPhases.REQUESTED -> "Requested"
    RequestPhases.APPROVED -> "Approved"
    RequestPhases.SEARCHING -> "Searching"
    RequestPhases.DOWNLOADING -> "Downloading"
    RequestPhases.IMPORTING -> "Importing"
    RequestPhases.PARTIALLY_AVAILABLE -> "Partly available"
    RequestPhases.AVAILABLE -> "Available"
    RequestPhases.DECLINED -> "Declined"
    RequestPhases.FAILED -> "Failed"
    RequestPhases.STALLED -> "Stalled"
    RequestPhases.UNKNOWN -> "Status unknown"
    else -> phase.replace('_', ' ').replaceFirstChar(Char::uppercase)
}

/** One or two words for a request's state where no progress is known (older servers). */
fun requestStateLabel(state: String?): String? = requestPhaseLabel(phaseOfState(state))

/** Good news, something that needs a look, or neither. The chip's words always say which: the tone only adds to it. */
enum class RequestTone { Ok, Warn, Plain }

fun requestTone(phase: String?): RequestTone = when (phase) {
    RequestPhases.AVAILABLE -> RequestTone.Ok
    RequestPhases.FAILED, RequestPhases.DECLINED, RequestPhases.STALLED -> RequestTone.Warn
    else -> RequestTone.Plain
}

/** The download bar: [percent] 0 to 100, or null when the sizes are not known (the bar then only shows movement). */
data class RequestBar(val percent: Int?) {
    /** 0f to 1f; null for an indeterminate bar. */
    val fraction: Float? get() = percent?.let { it.coerceIn(0, 100) / 100f }
}

/**
 * Everything a request's status shows: the chip, what the server adds beside it, and while it is
 * downloading the bar and its caption ("42% · about 12 min left").
 */
data class RequestStatusModel(
    val phase: String,
    val label: String,
    val tone: RequestTone,
    /** The server's detail and, for an unknown status, what was last known. */
    val secondary: String? = null,
    /** Only while downloading. */
    val bar: RequestBar? = null,
    val caption: String? = null,
)

/**
 * "about 12 min left"; null when there is no time, or it has passed. Counted from when the server
 * last checked, not from this TV's clock: both times come from one answer, so a clock that is off
 * here cannot make the wait look longer or shorter.
 */
fun requestEtaText(eta: String?, checkedAt: String?): String? {
    val due = eta?.let(::parseInstant) ?: return null
    val checked = checkedAt?.let(::parseInstant) ?: return null
    val leftMs = due.toEpochMilli() - checked.toEpochMilli()
    if (leftMs <= 0) return null
    val minutes = Math.round(leftMs / 60_000.0)
    return when {
        minutes < 1 -> "less than a minute left"
        minutes < 60 -> "about $minutes min left"
        else -> "about ${minutes / 60} hr${(minutes % 60).let { if (it > 0) " $it min" else "" }} left"
    }
}

private fun parseInstant(iso: String): Instant? = runCatching { Instant.parse(iso) }.getOrNull()
    ?: runCatching { java.time.OffsetDateTime.parse(iso).toInstant() }.getOrNull()

fun requestStatus(progress: RequestProgress): RequestStatusModel {
    val phase = progress.phase.ifBlank { RequestPhases.UNKNOWN }
    val downloading = phase == RequestPhases.DOWNLOADING
    val percent = progress.percent?.takeIf { downloading }?.coerceIn(0, 100)
    val lastKnown = progress.lastPhase?.takeIf { phase == RequestPhases.UNKNOWN && it.isNotBlank() }
        ?.let { "Last known: ${requestPhaseLabel(it)?.lowercase()}." }
    return RequestStatusModel(
        phase = phase,
        label = requestPhaseLabel(phase) ?: "Status unknown",
        tone = requestTone(phase),
        secondary = listOfNotNull(progress.detail?.trim()?.takeIf(String::isNotEmpty), lastKnown).joinToString(" ").takeIf(String::isNotEmpty),
        bar = if (downloading) RequestBar(percent) else null,
        caption = if (downloading) listOfNotNull(percent?.let { "$it%" }, requestEtaText(progress.eta, progress.checkedAt)).joinToString(" · ").takeIf(String::isNotEmpty) else null,
    )
}

/** The status of a poster: the chip names the percentage itself ("Downloading 42%"), and the bar is thin. */
fun requestStatus(progress: TileRequestProgress): RequestStatusModel =
    requestStatus(RequestProgress(phase = progress.phase, percent = progress.percent)).let { status ->
        status.copy(label = status.label + (status.bar?.percent?.let { " $it%" } ?: ""), caption = null)
    }

/** A request's progress, or what its state alone tells when the server sent none. */
fun MediaRequest.progressOrState(): RequestProgress =
    progress ?: RequestProgress(phase = phaseOfState(state) ?: RequestPhases.UNKNOWN, detail = note?.takeIf { state == RequestStates.DECLINED || state == RequestStates.FAILED })

/** What the title's request is doing, while the title cannot be played here; null when it can, or was never asked for. */
fun requestOf(tile: Tile): TileRequestProgress? {
    if (tile.requestState.isNullOrBlank() || tile.playableInApp || tile.kind == MediaKind.CHANNEL) return null
    return tile.requestProgress ?: phaseOfState(tile.requestState)?.let { TileRequestProgress(it) }
}

/**
 * The part of that a poster shows: a request in progress, or one that arrived before the library
 * has the title. Not old refusals: a declined or failed request leaves the poster bare.
 */
fun tileRequest(tile: Tile): TileRequestProgress? =
    if (isOpenRequest(tile.requestState) || tile.requestState == RequestStates.AVAILABLE) requestOf(tile) else null

/** While a download or an import is running the screen asks again every 15 s; otherwise every 3 minutes. */
const val REQUEST_POLL_MOVING_MS = 15_000L
const val REQUEST_POLL_IDLE_MS = 180_000L

fun requestPollMs(phases: Collection<String?>): Long =
    if (phases.any { it == RequestPhases.DOWNLOADING || it == RequestPhases.IMPORTING }) REQUEST_POLL_MOVING_MS else REQUEST_POLL_IDLE_MS

/** A request the server can usefully be asked about again: still open, or of a status that may have moved on. */
fun canCheckAgain(request: MediaRequest): Boolean =
    request.state == RequestStates.APPROVED || request.state == RequestStates.SENT ||
        request.progressOrState().phase in setOf(RequestPhases.STALLED, RequestPhases.UNKNOWN)

/** Requests screen order: what is moving first, then what is waiting, then what is over. Ties keep the server's order. */
fun byActivity(requests: List<MediaRequest>): List<MediaRequest> = requests.withIndex().sortedWith(
    compareBy({ activityRank(it.value.progressOrState().phase) }, { it.index }),
).map { it.value }

private fun activityRank(phase: String): Int = when (phase) {
    RequestPhases.DOWNLOADING, RequestPhases.IMPORTING -> 0
    RequestPhases.STALLED -> 1
    RequestPhases.AVAILABLE, RequestPhases.DECLINED, RequestPhases.FAILED -> 3
    else -> 2
}
