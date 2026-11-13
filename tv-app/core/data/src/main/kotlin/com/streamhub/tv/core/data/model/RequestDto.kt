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
