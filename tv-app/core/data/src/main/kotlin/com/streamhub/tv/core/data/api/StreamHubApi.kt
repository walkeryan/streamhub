package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.sameSwitchSession
import io.ktor.client.plugins.auth.AuthCircuitBreaker
import io.ktor.client.request.header
import io.ktor.http.HttpHeaders
import kotlinx.coroutines.CancellationException
import com.streamhub.tv.core.data.repo.boundProgressRequest
import com.streamhub.tv.core.data.repo.bindProgressSession
import com.streamhub.tv.core.data.auth.AccessTokenSource
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.*
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.delete
import io.ktor.client.request.get
import io.ktor.client.request.parameter
import io.ktor.client.request.patch
import io.ktor.client.request.post
import io.ktor.client.request.put
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.HttpStatusCode
import io.ktor.http.encodeURLPathPart
import io.ktor.http.isSuccess
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import javax.inject.Inject
import javax.inject.Singleton

class ApiException(val status: Int, val error: ApiError?, message: String, val retryAfterSeconds: Int? = null) : Exception(message)

/** 428 Precondition Required = "pending" for device-code and Plex PIN polling. */
sealed interface PollResult<out T> {
    data object Pending : PollResult<Nothing>
    data class Done<T>(val value: T) : PollResult<T>
}

/**
 * Hand-written Ktor binding of contracts/openapi.yaml. One function per operation.
 * Paths are relative to {server}/api/v1.
 */
@Singleton
class StreamHubApi @Inject constructor(
    private val client: HttpClient,
    private val session: SessionStore,
    private val webTokens: AccessTokenSource,
) {
    private suspend fun url(path: String): String {
        val base = session.current().apiBase ?: throw ApiException(0, null, "Server URL not set")
        return base + path
    }

    private fun id(v: String) = v.encodeURLPathPart()

    private suspend inline fun <reified T> HttpResponse.ok(): T {
        if (!status.isSuccess()) throw toError()
        return body()
    }

    private suspend fun HttpResponse.unit() {
        if (!status.isSuccess()) throw toError()
    }

    private suspend fun HttpResponse.toError(): ApiException {
        val text = runCatching { bodyAsText() }.getOrNull()
        val err = text?.let { runCatching { StreamHubJson.decodeFromString(ApiError.serializer(), it) }.getOrNull() }
        return ApiException(status.value, err, err?.message?.takeIf { it.isNotBlank() } ?: "HTTP ${status.value}", headers["Retry-After"]?.toIntOrNull())
    }

    private suspend inline fun <reified T> get(path: String, noinline block: HttpRequestBuilder.() -> Unit = {}): T =
        client.get(url(path), block).ok()

    // ---- Health / auth -------------------------------------------------------------------------

    /** Used by onboarding to validate a server URL before saving it. */
    suspend fun health(serverBase: String): Boolean =
        runCatching { client.get("$serverBase/api/v1/health").status.isSuccess() }.getOrDefault(false)

    /** Who this TV is signed in as and what they may do. Servers without users and roles answer 404. */
    suspend fun me(): Me = get("/auth/me")

    /** The people who can be picked on this shared TV. 404 = no such feature; 403 `not_shared_device` = a personal device. */
    suspend fun switchableUsers(): SwitchableUsers = get("/auth/switchable")

    /**
     * Become another person on this TV. The usual 401 handling is kept out of this one call: here a
     * 401 says the PIN is missing or wrong, and refreshing the session and sending the same PIN again
     * would count as a second wrong attempt. `pin` is sent as an explicit null when there is none.
     */
    suspend fun switchUser(from: Session, userId: String, pin: String?): AuthTokens {
        val base = from.apiBase ?: throw ApiException(0, null, "Server URL not set")
        val token = from.accessToken ?: throw ApiException(401, null, "Not signed in")
        return client.post("$base/auth/switch") {
            attributes.put(AuthCircuitBreaker, Unit)
            header(HttpHeaders.Authorization, "Bearer $token")
            setBody(buildJsonObject { put("user_id", userId); put("pin", pin) })
        }.ok()
    }

    suspend fun login(req: LoginRequest): AuthTokens = client.post(url("/auth/login")) { setBody(req) }.ok()
    suspend fun startDevicePairing(): DeviceCode = client.post(url("/auth/device")).ok()

    suspend fun pollDevicePairing(deviceCode: String): PollResult<AuthTokens> {
        val r = client.post(url("/auth/device/poll")) { setBody(DevicePollRequest(deviceCode)) }
        return when {
            r.status == HttpStatusCode.PreconditionRequired -> PollResult.Pending
            r.status.isSuccess() -> PollResult.Done(r.body())
            else -> throw r.toError()
        }
    }

    // ---- Profiles ------------------------------------------------------------------------------

    suspend fun profilePrefs(profileId: String): JsonObject = get("/profiles/${id(profileId)}/prefs")
    suspend fun putProfilePrefs(profileId: String, prefs: JsonObject): JsonObject =
        client.put(url("/profiles/${id(profileId)}/prefs")) { setBody(prefs) }.ok()

    suspend fun profiles(): List<Profile> = get("/profiles")
    suspend fun createProfile(p: Profile) = client.post(url("/profiles")) { setBody(p) }.unit()
    suspend fun updateProfile(p: Profile) = client.patch(url("/profiles/${id(p.id)}")) { setBody(p) }.unit()
    suspend fun deleteProfile(profileId: String) = client.delete(url("/profiles/${id(profileId)}")).unit()
    suspend fun homeLayout(profileId: String): HomeLayout = get("/profiles/${id(profileId)}/home_layout")
    suspend fun putHomeLayout(profileId: String, layout: HomeLayout): HomeLayout =
        client.put(url("/profiles/${id(profileId)}/home_layout")) { setBody(layout) }.ok()
    /**
     * Sets, changes or (with [pin] null) clears a profile's PIN. Changing or clearing an existing PIN of
     * one's own needs [currentPin]: 403 `current_pin_required` without it, 403 `pin_invalid` when it is
     * wrong (five guesses in fifteen minutes, then 429).
     */
    suspend fun setPin(profileId: String, pin: String?, currentPin: String? = null) =
        client.put(url("/profiles/${id(profileId)}/pin")) { setBody(PinUpdate(pin, currentPin)) }.unit()

    // ---- Disclaimer ----------------------------------------------------------------------------

    suspend fun disclaimer(): Disclaimer = get("/disclaimer")
    suspend fun acceptDisclaimer(version: String) =
        client.post(url("/disclaimer/accept")) { setBody(DisclaimerAccept(version)) }.unit()

    // ---- Sources -------------------------------------------------------------------------------

    suspend fun sources(): List<Source> = get("/sources")
    suspend fun source(sourceId: String): Source = get("/sources/${id(sourceId)}")

    /** Throws ApiException(412) if the disclaimer has not been accepted for IPTV/M3U. */
    suspend fun createSource(req: SourceCreate): Source = client.post(url("/sources")) { setBody(req) }.ok()
    suspend fun updateSource(sourceId: String, req: SourceCreate) =
        client.patch(url("/sources/${id(sourceId)}")) { setBody(req) }.unit()
    suspend fun deleteSource(sourceId: String) = client.delete(url("/sources/${id(sourceId)}")).unit()
    suspend fun syncSource(sourceId: String) = client.post(url("/sources/${id(sourceId)}/sync")).unit()
    suspend fun testSource(sourceId: String): SourceTestResult = client.post(url("/sources/${id(sourceId)}/test")).ok()
    suspend fun startPlexPin(): PlexPin = client.post(url("/sources/plex/pin")).ok()

    suspend fun pollPlexPin(pinId: Int): PollResult<List<Source>> {
        val r = client.get(url("/sources/plex/pin/$pinId"))
        return when {
            r.status == HttpStatusCode.PreconditionRequired -> PollResult.Pending
            r.status.isSuccess() -> PollResult.Done(r.body())
            else -> throw r.toError()
        }
    }

    suspend fun serviceRegistry(): List<ServiceRegistryEntry> = get("/services/registry")

    // ---- Catalog -------------------------------------------------------------------------------

    suspend fun home(): HomeResponse = get("/home")

    /**
     * One search across titles, channels and guide broadcasts. Asked twice per query: [discover] false
     * for what the hub holds, then true for the titles the metadata provider adds (see [searchParameters]).
     */
    suspend fun search(q: String, discover: Boolean, expand: Collection<String> = emptyList()): SearchResponse = get("/search") {
        searchParameters(q, discover, expand).forEach { (key, value) -> parameter(key, value) }
    }

    suspend fun title(titleId: String): TitleDetail = get("/titles/${id(titleId)}")

    suspend fun browse(
        section: String,
        sourceId: String? = null,
        genre: String? = null,
        sort: String? = null,
        page: Int = 1,
        language: String? = null,
        category: String? = null,
        q: String? = null,
    ): BrowsePage = get("/browse/${id(section)}") {
        browseParameters(sourceId, genre, sort, page, language, category, q).forEach { (key, value) -> parameter(key, value) }
    }

    /** Provider categories in server order, with scoped title counts: the shelves of Movies and Shows. */
    suspend fun browseCategories(section: String, sourceId: String? = null, language: String? = null, q: String? = null): List<CatalogFacet> =
        get("/browse/${id(section)}/categories") {
            browseFacetParameters(sourceId, language, q = q).forEach { (key, value) -> parameter(key, value) }
        }

    suspend fun discover(
        section: String,
        filters: NativeDiscoveryFilters = NativeDiscoveryFilters(),
        page: Int = 1,
    ): DiscoveryPage {
        require(section == "movies" || section == "shows")
        return get("/discover/${id(section)}") {
            discoveryParameters(filters, page).forEach { (key, value) -> parameter(key, value) }
        }
    }

    suspend fun browseGenres(section: String, sourceId: String? = null, language: String? = null, category: String? = null, q: String? = null): List<CatalogFacet> =
        get("/browse/${id(section)}/genres") {
            browseFacetParameters(sourceId, language, category, q).forEach { (key, value) -> parameter(key, value) }
        }
    suspend fun browseLanguages(section: String, sourceId: String? = null): List<LanguageCount> =
        get("/browse/${id(section)}/languages") { sourceId?.let { parameter("source_id", it) } }
    suspend fun sportsRecordings(filters: NativeSportsFilters, offset: Int = 0, limit: Int = 40): SportsRecordingsPage = get("/sports/recordings") {
        sportsRecordingParameters(filters,offset,limit).forEach { (key,value) -> parameter(key,value) }
    }

    /** Live, upcoming and game-channel events for [days] days from [date], days being the viewer's own ([timezone]). */
    suspend fun sportsEvents(filters: NativeSportsFilters, date: String, days: Int, timezone: String, offset: Int = 0, limit: Int = 100): SportsEventsPage = get("/sports/events") {
        sportsEventParameters(filters, date, days, timezone, offset, limit).forEach { (key, value) -> parameter(key, value) }
    }

    /** Always-on sports networks, by channel number. */
    suspend fun sportsChannels(language: String? = null, q: String? = null, limit: Int = 24): SportsChannelsPage = get("/sports/channels") {
        language?.let { parameter("language", it) }
        q?.trim()?.take(200)?.takeIf { it.isNotEmpty() }?.let { parameter("q", it) }
        parameter("limit", limit)
    }

    suspend fun sports(language: String? = null, sport: String? = null, offset: Int = 0, limit: Int = 40): SportsPage = get("/sports") {
        language?.let { parameter("language", it) }
        sport?.let { parameter("sport", it) }
        parameter("offset", offset)
        parameter("limit", limit)
    }

    // ---- Live TV -------------------------------------------------------------------------------

    suspend fun channels(
        sourceId: String? = null,
        group: String? = null,
        languages: List<String>? = null,
        favorites: Boolean? = null,
        q: String? = null,
        listId: String? = null,
        hasEpg: Boolean? = null,
        limit: Int? = null,
        offset: Int? = null,
        country: String? = null,
    ): ChannelList = get("/channels") {
        listId?.let { parameter("list_id", it) }
        hasEpg?.let { parameter("has_epg", it) }
        limit?.let { parameter("limit", it) }
        offset?.let { parameter("offset", it) }
        sourceId?.let { parameter("source_id", it) }
        group?.let { parameter("group", it) }
        applyLiveRegionLanguage(languages, country)
        favorites?.let { parameter("favorites", it) }
        q?.let { parameter("q", it) }
    }

    suspend fun channelGroups(languages: List<String>? = null, favorites: Boolean? = null, country: String? = null): List<ChannelGroup> = get("/channels/groups") {
        applyLiveRegionLanguage(languages, country)
        favorites?.let { parameter("favorites", it) }
    }
    suspend fun patchChannelGroup(groupId: String, patch: ChannelGroupPatch) =
        client.patch(url("/channels/groups/${id(groupId)}")) { setBody(patch) }.unit()
    suspend fun channelLanguages(): List<LanguageCount> = get("/channels/languages")
    suspend fun patchChannel(channel: Channel) = client.patch(url("/channels/${id(channel.id)}")) { setBody(channel) }.unit()

    suspend fun epg(channelIds: List<String>, from: String, to: String, country: String? = null): Map<String, List<Programme>> = get("/epg") {
        country?.let { parameter("country", it) }
        parameter("channel_ids", channelIds.joinToString(","))
        parameter("from", from)
        parameter("to", to)
    }

    // ---- Playback ------------------------------------------------------------------------------

    suspend fun acquireBuffer(token: String): BufferLease = client.post(url("/playback/buffer/lease")) { setBody(BufferLeaseRequest(token)) }.ok()
    suspend fun heartbeatBuffer(action: BufferLeaseAction): BufferWindow = client.post(url("/playback/buffer/heartbeat")) { setBody(action) }.ok()
    suspend fun seekBuffer(action: BufferLeaseAction): BufferSeekResult = client.post(url("/playback/buffer/seek")) { setBody(action) }.ok()
    suspend fun releaseBuffer(action: BufferLeaseAction) { client.post(url("/playback/buffer/release")) { setBody(action) }.ok<Unit>() }

    suspend fun authConfig(): AuthConfig = get("/auth/config")
    suspend fun switchProfile(request: LoginRequest): AuthTokens = client.post(url("/auth/profile-switch")) { setBody(request) }.ok()
    suspend fun startOwnerPin(profileId: String): OwnerPinChallenge = client.post(url("/auth/owner-pin/start")) { setBody(OwnerPinStart(profileId)) }.ok()
    suspend fun verifyOwnerPin(request: OwnerPinVerify): AuthTokens = client.post(url("/auth/owner-pin/verify")) { setBody(request) }.ok()
    suspend fun cancelOwnerPin(request: OwnerPinCancel) { client.post(url("/auth/owner-pin/cancel")) { setBody(request) }.unit() }


    suspend fun rewindCapability(id: String): RewindCapability = get("/playback/rewind") { parameter("id", id) }

    suspend fun playbackOptions(id: String, sourceId: String? = null): PlaybackOptions = get("/playback/options") {
        parameter("id", id)
        sourceId?.let { parameter("source_id", it) }
    }

    suspend fun resolve(req: PlaybackRequest): PlaybackDescriptor =
        client.post(url("/playback/resolve")) { setBody(req) }.ok()

    suspend fun playbackGeneration():PlaybackGeneration=get("/playback/generation")
    suspend fun reserveMultiview(request:MultiviewRequest,owner:Session):MultiviewDescriptor {
        val current=session.current()
        if(!sameSwitchSession(owner,current)) throw CancellationException("Playback session changed")
        val base=owner.apiBase ?: throw CancellationException("Playback server unavailable")
        val token=current.accessToken ?: throw CancellationException("Playback sign-in required")
        return client.post("$base/playback/multiview") {
            attributes.put(AuthCircuitBreaker,Unit)
            header(HttpHeaders.Authorization,"Bearer $token")
            setBody(request)
        }.ok()
    }
    suspend fun releaseMultiview(leaseId:String,owner:Session) {
        val current=session.current()
        val token=(if(sameSwitchSession(owner,current))current else owner).accessToken ?: return
        val base=owner.apiBase ?: return
        client.post("$base/playback/multiview/release") {
            attributes.put(AuthCircuitBreaker,Unit)
            header(HttpHeaders.Authorization,"Bearer $token")
            setBody(MultiviewRelease(leaseId))
        }.unit()
    }

    suspend fun reportProgress(report: ProgressReport, owner:Session):Boolean {
        val request=boundProgressRequest(owner,session.current(),report) ?: return false
        val response=client.post(request.url) {
            bindProgressSession(request)
        }
        // A report carries its token outside the bearer plugin, so an access token that aged out during
        // a long film is renewed here: the web app is asked for a current one and the report sent once more,
        // still bound to the same owner (a token for another person is not sent).
        if(response.status==HttpStatusCode.Unauthorized && session.current().webOwned) {
            webTokens.currentAccessToken(request.accessToken) ?: response.unit()
            val again=boundProgressRequest(owner,session.current(),report) ?: return false
            client.post(again.url) { bindProgressSession(again) }.unit()
            return true
        }
        response.unit()
        return true
    }

    // ---- Watch rooms ---------------------------------------------------------------------------

    /** A room for a title or channel; it starts empty and paused at 0 (a live room starts playing). */
    suspend fun createRoom(titleId: String, variantId: String? = null): Room =
        client.post(url("/rooms")) { setBody(RoomCreate(titleId, variantId)) }.ok()
    /** The join screen: title, host, members, state. 404 `room_not_found`. */
    suspend fun room(code: String): Room = get("/rooms/${id(code)}")
    /** Takes a seat; resolves playback exactly as /playback/resolve would and returns its refusals unchanged. */
    suspend fun joinRoom(code: String, allowUnverifiedAudio: Boolean = false, memberToken: String? = null): RoomJoined =
        client.post(url("/rooms/${id(code)}/join")) { setBody(RoomJoinRequest(allowUnverifiedAudio = allowUnverifiedAudio, memberToken = memberToken)) }.ok()
    /**
     * A member's descriptor as it is now; the seat is unchanged. Authorized like the room's socket, by
     * the member token in the query: no bearer token is sent (see PUBLIC_PATHS) and a 401 here
     * (`room_unauthorized`) is about that token, so it is kept away from the session refresh.
     */
    suspend fun roomDescriptor(code: String, memberToken: String, allowUnverifiedAudio: Boolean = false): PlaybackDescriptor =
        client.get(url("/rooms/${id(code)}/descriptor")) {
            attributes.put(AuthCircuitBreaker, Unit)
            parameter("member_token", memberToken)
            parameter("client", "android_tv")
            if (allowUnverifiedAudio) parameter("allow_unverified_audio", true)
        }.ok()
    /** Host or administrator: closes the room for everyone. */
    suspend fun deleteRoom(code: String) = client.delete(url("/rooms/${id(code)}")).unit()

    // ---- Recordings ----------------------------------------------------------------------------

    /** The current profile's recordings (everyone's for an admin), earliest first; cancelled ones are left out. */
    suspend fun recordings(): RecordingsResponse = get("/recordings")
    /** 201 scheduled, or 200 when this programme was already scheduled: both answer with the recording. */
    suspend fun record(req: RecordRequest): Recording = client.post(url("/recordings")) { setBody(req) }.ok()
    /** Cancels an upcoming or running recording; deletes a finished one and its file. */
    suspend fun deleteRecording(recordingId: String) = client.delete(url("/recordings/${id(recordingId)}")).unit()
    suspend fun recordingRules(): RecordingRulesResponse = get("/recording-rules")
    suspend fun createRecordingRule(req: RecordingRuleCreate): RecordingRule = client.post(url("/recording-rules")) { setBody(req) }.ok()
    suspend fun patchRecordingRule(ruleId: String, patch: RecordingRulePatch): RecordingRule =
        client.patch(url("/recording-rules/${id(ruleId)}")) { setBody(patch) }.ok()
    suspend fun deleteRecordingRule(ruleId: String) = client.delete(url("/recording-rules/${id(ruleId)}")).unit()

    // ---- Requests / notices ---------------------------------------------------------------------

    /** Newest first. [all] lists everyone's and needs permission to approve. */
    suspend fun requests(all: Boolean = false): RequestsResponse = get("/requests") { parameter("scope", if (all) "all" else "mine") }
    /** 201 created, or 200 when an open request for this title already exists: both answer with the request. */
    suspend fun createRequest(req: RequestCreate): MediaRequest = client.post(url("/requests")) { setBody(req) }.ok()
    /** One request, for its requester or whoever may approve; its stored progress, refreshed first when stale. */
    suspend fun request(requestId: String): MediaRequest = get("/requests/${id(requestId)}")
    /** Asks the request manager about this request now (at most every 5 s; sooner calls answer the stored progress). */
    suspend fun refreshRequest(requestId: String): MediaRequest = client.post(url("/requests/${id(requestId)}/refresh")).ok()
    suspend fun approveRequest(requestId: String, note: String? = null): MediaRequest =
        client.post(url("/requests/${id(requestId)}/approve")) { setBody(RequestDecision(note)) }.ok()
    suspend fun declineRequest(requestId: String, note: String? = null): MediaRequest =
        client.post(url("/requests/${id(requestId)}/decline")) { setBody(RequestDecision(note)) }.ok()
    /** Notices for the current profile, unread first. */
    suspend fun notices(): NoticesResponse = get("/notices")
    suspend fun markNoticeRead(noticeId: String) = client.post(url("/notices/${id(noticeId)}/read")).unit()

    // ---- Favorites / watchlist / history -------------------------------------------------------

    suspend fun favorites(): List<Tile> = get("/favorites")
    suspend fun addFavorite(f: Favorite) = client.put(url("/favorites")) { setBody(f) }.unit()
    suspend fun removeFavorite(kind: MediaKind, itemId: String) = client.delete(url("/favorites")) {
        parameter("kind", kind.wire); parameter("id", itemId)
    }.unit()
    suspend fun reorderFavorites(list: List<Favorite>) =
        client.post(url("/favorites/reorder")) { setBody(list) }.unit()

    suspend fun watchlist(): List<Tile> = get("/watchlist")
    suspend fun addToWatchlist(itemId: String) = client.put(url("/watchlist")) { setBody(WatchlistPut(itemId)) }.unit()
    suspend fun removeFromWatchlist(itemId: String) =
        client.delete(url("/watchlist")) { parameter("id", itemId) }.unit()
    suspend fun history(): List<Tile> = get("/history")

    // ---- Themes / settings ---------------------------------------------------------------------

    suspend fun themes(): List<Theme> = get("/themes")
    /** Version + TMDB/JustWatch attribution strings (schema open in the contract). */
    suspend fun about(): JsonObject = get("/about")
    suspend fun settings(): Settings = get("/settings")
    /** Newest TV build on offer. Servers without the update service answer 404 (see UpdateRepository). */
    suspend fun tvAppRelease(): TvAppRelease = get("/app/tv")
    suspend fun patchSettings(s: Settings) = client.patch(url("/settings")) { setBody(s) }.unit()
    suspend fun setTmdbKey(key: String) =
        client.put(url("/settings/tmdb_key")) { setBody(JsonObject(mapOf("api_key" to kotlinx.serialization.json.JsonPrimitive(key)))) }.unit()
}
