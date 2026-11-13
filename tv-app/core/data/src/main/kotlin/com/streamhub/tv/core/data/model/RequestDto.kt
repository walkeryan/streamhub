package com.streamhub.tv.core.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/* Title requests and notices (GET/POST /requests, approve/decline, GET /notices, POST /notices/{id}/read). */

/** RequestState values, as text: a state this build does not know still decodes. */
object RequestStates {
    const val PENDING_APPROVAL = "pending_approval"
    const val APPROVED = "approved"
    const val SENT = "sent"
    const val AVAILABLE = "available"
    const val DECLINED = "declined"
    const val FAILED = "failed"
}

/**
 * RequestPhase values, as text: what is happening to a requested title, as the request manager last
 * reported it. A phase this build does not know still decodes and is shown by its own name.
 */
object RequestPhases {
    /** Waiting for approval here. */
    const val REQUESTED = "requested"
    /** Approved here; the request manager has not taken it yet. */
    const val APPROVED = "approved"
    /** The request manager has it and nothing is in its download queue. */
    const val SEARCHING = "searching"
    const val DOWNLOADING = "downloading"
    /** Every queue item has finished and the title is not available yet. */
    const val IMPORTING = "importing"
    /** Series only: some of the requested seasons can be watched. */
    const val PARTIALLY_AVAILABLE = "partially_available"
    const val AVAILABLE = "available"
    const val DECLINED = "declined"
    const val FAILED = "failed"
    /** Every unfinished download reports a problem, or nothing has moved for 30 minutes. */
    const val STALLED = "stalled"
    /** The request manager did not answer; [RequestProgress.lastPhase] has the phase before. */
    const val UNKNOWN = "unknown"
}

/** What a request is doing right now (MediaRequest.progress). */
@Serializable
data class RequestProgress(
    val phase: String = RequestPhases.UNKNOWN,
    /** Only with phase unknown: the last phase that was known. */
    @SerialName("last_phase") val lastPhase: String? = null,
    /** Only while downloading: 0 to 100. Null when a size is missing: the bar is indeterminate. */
    val percent: Int? = null,
    /** Only while downloading: when the last queue item is due. */
    val eta: String? = null,
    /** Short plain text for beside the phase: "2 of 8 episodes", "Queued", why it stalled, the decline note. */
    val detail: String? = null,
    /** When the request manager last answered for this request. */
    @SerialName("checked_at") val checkedAt: String? = null,
)

/** The part of [RequestProgress] a poster needs (Tile.request_progress). */
@Serializable
data class TileRequestProgress(val phase: String = RequestPhases.UNKNOWN, val percent: Int? = null)

@Serializable
data class MediaRequest(
    val id: String,
    @SerialName("profile_id") val profileId: String = "",
    @SerialName("profile_name") val profileName: String? = null,
    /** movie | tv */
    @SerialName("media_type") val mediaType: String = "",
    @SerialName("tmdb_id") val tmdbId: Long = 0,
    /** Hub id of the requested title, e.g. tmdb:movie:603. */
    @SerialName("title_id") val titleId: String = "",
    val title: String = "",
    val year: Int? = null,
    val poster: String? = null,
    /** tv only; null = whole series. */
    val seasons: List<Int>? = null,
    val state: String = RequestStates.PENDING_APPROVAL,
    /** Decline reason, or why an approved request has not reached the request manager yet. */
    val note: String? = null,
    /** What it is doing right now. Null only from a server older than request progress. */
    val progress: RequestProgress? = null,
)

@Serializable
data class RequestsResponse(
    val requests: List<MediaRequest> = emptyList(),
    /** The current profile may approve or decline requests and list scope=all; absent on servers that do not say. */
    @SerialName("can_approve") val canApprove: Boolean? = null,
)

/** POST /requests. `seasons` left out = the whole series. */
@Serializable
data class RequestCreate(
    @SerialName("media_type") val mediaType: String,
    @SerialName("tmdb_id") val tmdbId: Long,
    val seasons: List<Int>? = null,
)

@Serializable
data class RequestDecision(val note: String? = null)

@Serializable
data class Notice(
    val id: String,
    /** request_available | request_declined */
    val kind: String = "",
    val title: String = "",
    val body: String? = null,
    /** Hub id the notice is about. */
    @SerialName("ref_id") val refId: String? = null,
    @SerialName("read_at") val readAt: String? = null,
)

@Serializable
data class NoticesResponse(val notices: List<Notice> = emptyList())
