package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.MediaRequest
import com.streamhub.tv.core.data.model.Notice
import com.streamhub.tv.core.data.model.Permissions
import com.streamhub.tv.core.data.model.RequestCreate
import com.streamhub.tv.core.data.model.RequestStates
import com.streamhub.tv.core.data.model.Tile
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CancellationException
import javax.inject.Inject
import javax.inject.Singleton

data class RequestList(val requests: List<MediaRequest>, val canApprove: Boolean)

/** Asking for titles no source has, and the notices that answer; an interface so view models are tested against a fake. */
interface RequestsRepository {
    suspend fun mine(): RequestList
    /** Everyone's requests; needs permission to approve. */
    suspend fun all(): List<MediaRequest>
    /** [seasons] null or empty = the whole series (and always for a film). */
    suspend fun request(target: RequestTarget, seasons: List<Int>? = null): MediaRequest
    suspend fun approve(requestId: String): MediaRequest
    suspend fun decline(requestId: String): MediaRequest
    /** Unread notices, newest as the server orders them. Empty on a server without notices. */
    suspend fun unreadNotices(): List<Notice>
    suspend fun markRead(noticeId: String)
}

@Singleton
class DefaultRequestsRepository @Inject constructor(
    private val api: StreamHubApi,
    private val me: MeRepository,
) : RequestsRepository {
    override suspend fun mine(): RequestList = api.requests().let { RequestList(it.requests, it.canApprove ?: me.can(Permissions.REQUESTS_APPROVE)) }
    override suspend fun all(): List<MediaRequest> = api.requests(all = true).requests
    override suspend fun request(target: RequestTarget, seasons: List<Int>?): MediaRequest =
        api.createRequest(RequestCreate(target.mediaType, target.tmdbId, seasons?.takeIf { target.mediaType == "tv" && it.isNotEmpty() }?.sorted()))
    override suspend fun approve(requestId: String): MediaRequest = api.approveRequest(requestId)
    override suspend fun decline(requestId: String): MediaRequest = api.declineRequest(requestId)
    override suspend fun unreadNotices(): List<Notice> =
        try { api.notices().notices.filter { it.readAt == null } }
        catch (e: CancellationException) { throw e }
        // A server without notices (404) or a user who may not see them (403) simply has none.
        catch (e: ApiException) { if (e.status == 404 || e.status == 403) emptyList() else throw e }
    override suspend fun markRead(noticeId: String) = api.markNoticeRead(noticeId)
}

@Module
@InstallIn(SingletonComponent::class)
abstract class RequestsModule {
    @Binds abstract fun requests(impl: DefaultRequestsRepository): RequestsRepository
}

// ---- Rules shared by Details, Search and the Requests screen (pure, unit-tested) -----------------

/** What POST /requests needs to name a title. */
data class RequestTarget(val mediaType: String, val tmdbId: Long)

/**
 * The request target of a hub id: `tmdb:movie:603` → movie 603, `tmdb:tv:1399` → tv 1399.
 * null for an id that is not a TMDB one (a title only one source knows cannot be requested).
 */
fun requestTarget(titleId: String): RequestTarget? {
    val parts = titleId.split(':')
    if (parts.size != 3 || parts[0] != "tmdb" || parts[1] !in setOf("movie", "tv")) return null
    val id = parts[2].toLongOrNull()?.takeIf { it > 0 } ?: return null
    return RequestTarget(parts[1], id)
}

/** A request that is still on its way: its state is shown instead of offering Request again. */
fun isOpenRequest(state: String?): Boolean =
    state == RequestStates.PENDING_APPROVAL || state == RequestStates.APPROVED || state == RequestStates.SENT

/** One or two words for a tile badge and a list row; null when the title was never requested. */
fun requestStateLabel(state: String?): String? = when (state) {
    null, "" -> null
    RequestStates.PENDING_APPROVAL -> "Requested"
    RequestStates.APPROVED -> "Approved"
    RequestStates.SENT -> "On its way"
    RequestStates.AVAILABLE -> "Available"
    RequestStates.DECLINED -> "Declined"
    RequestStates.FAILED -> "Request failed"
    else -> state.replace('_', ' ').replaceFirstChar(Char::uppercase)
}

/** The sentence under the Details actions. */
fun requestStateText(state: String?): String? = when (state) {
    null, "" -> null
    RequestStates.PENDING_APPROVAL -> "Requested. Waiting for approval."
    RequestStates.APPROVED -> "Request approved. It will be fetched soon."
    RequestStates.SENT -> "Request approved and on its way. You'll get a notice on Home when it can be watched."
    RequestStates.AVAILABLE -> "Your request was fulfilled: this title is available."
    RequestStates.DECLINED -> "This request was declined. It can be asked for again."
    RequestStates.FAILED -> "This request failed. It can be asked for again."
    else -> "Request: ${requestStateLabel(state)}."
}

/**
 * The badge on a search tile with no in-app source: where its request stands, or "Not available".
 * null for a title that plays here.
 */
fun availabilityBadge(tile: Tile): String? =
    if (tile.playableInApp || tile.kind == MediaKind.CHANNEL) null else requestStateLabel(tile.requestState) ?: "Not available"

/** Whether "pick seasons" is offered: a series whose detail lists seasons a request may choose from. */
fun canPickSeasons(target: RequestTarget?, requestSeasons: List<Int>): Boolean = target?.mediaType == "tv" && requestSeasons.isNotEmpty()
