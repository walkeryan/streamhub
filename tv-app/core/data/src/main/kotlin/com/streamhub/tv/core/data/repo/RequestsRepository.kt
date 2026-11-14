package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.model.MediaRequest
import com.streamhub.tv.core.data.model.Notice
import com.streamhub.tv.core.data.model.Permissions
import com.streamhub.tv.core.data.model.RequestCreate
import com.streamhub.tv.core.data.model.RequestStates
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
    /** One request as it is now (GET /requests/{id}); the server refreshes its progress first when it is stale. */
    suspend fun one(requestId: String): MediaRequest
    /** "Check again": asks the request manager about this request now. */
    suspend fun refresh(requestId: String): MediaRequest
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
    override suspend fun one(requestId: String): MediaRequest = api.request(requestId)
    override suspend fun refresh(requestId: String): MediaRequest = api.refreshRequest(requestId)
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

/** A request that is still open: where it stands is shown instead of offering Request again. */
fun isOpenRequest(state: String?): Boolean =
    state == RequestStates.PENDING_APPROVAL || state == RequestStates.APPROVED || state == RequestStates.SENT

/** Whether "pick seasons" is offered: a series whose detail lists seasons a request may choose from. */
fun canPickSeasons(target: RequestTarget?, requestSeasons: List<Int>): Boolean = target?.mediaType == "tv" && requestSeasons.isNotEmpty()
