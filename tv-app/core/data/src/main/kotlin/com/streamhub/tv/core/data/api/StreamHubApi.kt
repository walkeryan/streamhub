package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.sameSwitchSession
import io.ktor.client.plugins.auth.AuthCircuitBreaker
import io.ktor.client.request.header
import io.ktor.http.HttpHeaders
import kotlinx.coroutines.CancellationException
import com.streamhub.tv.core.data.repo.boundProgressRequest
import com.streamhub.tv.core.data.repo.bindProgressSession
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
        return ApiException(status.value, err, err?.message ?: "HTTP ${status.value}", headers["Retry-After"]?.toIntOrNull())
    }

    private suspend inline fun <reified T> get(path: String, noinline block: HttpRequestBuilder.() -> Unit = {}): T =
        client.get(url(path), block).ok()

    // ---- Health / auth -------------------------------------------------------------------------

    /** Used by onboarding to validate a server URL before saving it. */
    suspend fun health(serverBase: String): Boolean =
        runCatching { client.get("$serverBase/api/v1/health").status.isSuccess() }.getOrDefault(false)

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
    suspend fun setPin(profileId: String, pin: String?) =
        client.put(url("/profiles/${id(profileId)}/pin")) { setBody(PinUpdate(pin)) }.unit()

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

    suspend fun search(q: String, kinds: List<MediaKind>? = null, limit: Int = 40, languages: List<String>? = null, sourceId: String? = null): SearchResponse = get("/search") {
        parameter("q", q)
        applySearchFilters(languages, sourceId)
        kinds?.let { k -> parameter("kinds", k.joinToString(",") { it.wire }) }
        parameter("limit", limit)
    }

    suspend fun title(titleId: String): TitleDetail = get("/titles/${id(titleId)}")

    suspend fun browse(
        section: String,
        sourceId: String? = null,
        genre: String? = null,
        sort: String? = null,
        page: Int = 1,
        language: String? = null,
    ): BrowsePage = get("/browse/${id(section)}") {
        sourceId?.let { parameter("source_id", it) }
        genre?.let { parameter("genre", it) }
        sort?.let { parameter("sort", it) }
        parameter("page", page)
        language?.let { parameter("language", it) }
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

    suspend fun browseGenres(section: String, sourceId: String? = null, language: String? = null): List<CatalogFacet> =
        get("/browse/${id(section)}/genres") {
            sourceId?.let { parameter("source_id", it) }
            language?.let { parameter("language", it) }
        }
    suspend fun browseLanguages(section: String, sourceId: String? = null): List<LanguageCount> =
        get("/browse/${id(section)}/languages") { sourceId?.let { parameter("source_id", it) } }
    suspend fun sportsRecordings(filters: NativeSportsFilters, offset: Int = 0, limit: Int = 40): SportsRecordingsPage = get("/sports/recordings") {
        sportsRecordingParameters(filters,offset,limit).forEach { (key,value) -> parameter(key,value) }
    }

    suspend fun sportsEvents(filters: NativeSportsFilters, offset: Int = 0, limit: Int = 40): SportsEventsPage = get("/sports/events") {
        filters.language?.let { parameter("language", it) }
        filters.sport?.let { parameter("sport", it) }
        filters.query.trim().take(200).takeIf { it.isNotEmpty() }?.let { parameter("q",it) }
        filters.date?.let { parameter("date", it) }
        parameter("timezone", "America/New_York")
        parameter("status", filters.status)
        parameter("offset", offset)
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
        client.post(request.url) {
            bindProgressSession(request)
        }.unit()
        return true
    }

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
    suspend fun patchSettings(s: Settings) = client.patch(url("/settings")) { setBody(s) }.unit()
    suspend fun setTmdbKey(key: String) =
        client.put(url("/settings/tmdb_key")) { setBody(JsonObject(mapOf("api_key" to kotlinx.serialization.json.JsonPrimitive(key)))) }.unit()
}
