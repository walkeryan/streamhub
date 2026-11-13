package com.streamhub.tv.core.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/*
 * Hand-written DTOs mirroring contracts/openapi.yaml (StreamHub API 1.0.0).
 * Field names follow the wire format via @SerialName; unknown keys are ignored by the Json config
 * so the server can add fields without breaking older TV builds.
 */

/** One stream holding a source connection, named in a `source_busy` error when the server knows it. */
@Serializable
data class ActiveStream(
    @SerialName("profile_name") val profileName: String? = null,
    val title: String? = null,
)

@Serializable
data class ApiError(
    val code: String = "",
    val message: String = "",
    /** Optional on `source_busy` and `user_stream_limit`: the streams in the way right now. */
    val active: List<ActiveStream> = emptyList(),
    /** Optional on 403 `forbidden`: the permissions the caller lacks. */
    val missing: List<String> = emptyList(),
)

// ---- Auth ------------------------------------------------------------------------------------

@Serializable
data class AuthTokens(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    val profile: Profile,
)

@Serializable
data class DeviceCode(
    @SerialName("device_code") val deviceCode: String,
    @SerialName("user_code") val userCode: String,
    @SerialName("expires_in") val expiresIn: Int,
    val interval: Int,
)

@Serializable
data class LoginRequest(
    @SerialName("profile_id") val profileId: String,
    val pin: String? = null,
    val password: String? = null,
)

@Serializable
data class RefreshRequest(@SerialName("refresh_token") val refreshToken: String)

@Serializable
data class DevicePollRequest(@SerialName("device_code") val deviceCode: String)

// ---- Profiles ----------------------------------------------------------------------------------

@Serializable
data class Profile(
    val id: String,
    val name: String,
    val avatar: String? = null,
    @SerialName("is_admin") val isAdmin: Boolean = false,
    @SerialName("theme_id") val themeId: String = "midnight",
    val languages: List<String> = emptyList(),
    @SerialName("has_pin") val hasPin: Boolean = false,
    val kids: Boolean = false,
    @SerialName("disclaimer_accepted_version") val disclaimerAcceptedVersion: String? = null,
    @SerialName("has_password") val hasPassword: Boolean = false,
    @SerialName("content_restricted") val contentRestricted: Boolean = false,
)

@Serializable
data class PinUpdate(val pin: String?)

// ---- Disclaimer --------------------------------------------------------------------------------

@Serializable
data class Disclaimer(val version: String, val text: String)

@Serializable
data class DisclaimerAccept(val version: String)

// ---- Sources -----------------------------------------------------------------------------------

@Serializable
enum class SourceType {
    @SerialName("plex") PLEX,
    @SerialName("jellyfin") JELLYFIN,
    @SerialName("xtream") XTREAM,
    @SerialName("m3u") M3U,
    @SerialName("streaming_service") STREAMING_SERVICE,
    ;

    val requiresDisclaimer: Boolean get() = this == XTREAM || this == M3U
}

@Serializable
data class Source(
    val id: String,
    val type: SourceType,
    val name: String,
    val enabled: Boolean,
    val priority: Int,
    val region: String = "US",
    val status: String,
    @SerialName("last_sync") val lastSync: String? = null,
    val counts: Map<String, Int> = emptyMap(),
    @SerialName("service_key") val serviceKey: String? = null,
    @SerialName("config_summary") val configSummary: JsonObject? = null,
)

@Serializable
data class SourceCreate(
    val type: SourceType,
    val name: String,
    val region: String? = null,
    @SerialName("service_key") val serviceKey: String? = null,
    val config: JsonObject,
)

@Serializable
data class SourceTestResult(val ok: Boolean = false, val message: String = "")

@Serializable
data class PlexPin(
    @SerialName("pin_id") val pinId: Int,
    val code: String,
    @SerialName("link_url") val linkUrl: String,
)

/**
 * GET /services/registry → ServiceInfo[] (contract 19d699d). android_title_uri / android_component
 * are not in ServiceInfo (title URIs are built server-side into PlaybackDescriptor.handoff); they're
 * kept optional here so the DEEPLINKS.md seed / a future registry editor can supply them.
 */
@Serializable
data class ServiceRegistryEntry(
    @SerialName("service_key") val key: String,
    val name: String,
    @SerialName("android_package") val androidPackage: String? = null,
    @SerialName("web_url") val webUrl: String = "",
    @SerialName("supports_title_link") val supportsTitleLink: Boolean = false,
    @SerialName("tmdb_provider_ids") val tmdbProviderIds: List<Int> = emptyList(),
    @SerialName("android_title_uri") val androidTitleUri: String? = null,
    @SerialName("android_component") val androidComponent: String? = null,
)

@Serializable
data class HomeLayoutRow(val id: String, val hidden: Boolean = false, val sort: Int? = null)

/** Per-profile Home row order / hidden rows: GET/PUT /profiles/{id}/home_layout. */
@Serializable
data class HomeLayout(val rows: List<HomeLayoutRow> = emptyList())

// ---- Catalog -----------------------------------------------------------------------------------

@Serializable
enum class MediaKind {
    @SerialName("movie") MOVIE,
    @SerialName("show") SHOW,
    @SerialName("season") SEASON,
    @SerialName("episode") EPISODE,
    @SerialName("channel") CHANNEL,
    @SerialName("vod") VOD,
    @SerialName("series") SERIES,
    ;

    val wire: String get() = name.lowercase()
}

@Serializable
data class SourceBadge(
    @SerialName("source_id") val sourceId: String,
    @SerialName("source_type") val sourceType: SourceType,
    val name: String,
    @SerialName("playable_in_app") val playableInApp: Boolean,
    val kind: String? = null,
    val quality: String? = null,
)

@Serializable
data class Tile(
    val id: String,
    val kind: MediaKind,
    val title: String,
    val subtitle: String? = null,
    val year: Int? = null,
    val poster: String? = null,
    val backdrop: String? = null,
    val logo: String? = null,
    val rating: Double? = null,
    @SerialName("progress_pct") val progressPct: Double? = null,
    @SerialName("is_favorite") val isFavorite: Boolean = false,
    val sources: List<SourceBadge> = emptyList(),
    @SerialName("now_playing") val nowPlaying: Programme? = null,
    /** The open request for this title, else its most recent closed one; null when never requested. */
    @SerialName("request_state") val requestState: String? = null,
    /** No in-app source, a request manager is configured and the profile may request. */
    val requestable: Boolean = false,
    /** What that same request is doing right now; set exactly when [requestState] is (by servers that report progress). */
    @SerialName("request_progress") val requestProgress: TileRequestProgress? = null,
) {
    val playableInApp: Boolean get() = sources.any { it.playableInApp }
}

@Serializable
enum class RowLayout {
    @SerialName("poster") POSTER,
    @SerialName("landscape") LANDSCAPE,
    @SerialName("hero") HERO,
    @SerialName("channel") CHANNEL,
}

@Serializable
data class Row(
    val id: String,
    val title: String,
    val layout: RowLayout = RowLayout.POSTER,
    val tiles: List<Tile> = emptyList(),
)

@Serializable
data class HomeResponse(val hero: List<Tile> = emptyList(), val rows: List<Row> = emptyList())

@Serializable
data class CastMember(val name: String, val character: String? = null, val photo: String? = null)

/** TitleDetail = Tile + detail fields (allOf in the contract, flattened here). */
@Serializable
data class TitleDetail(
    val id: String,
    val kind: MediaKind,
    val title: String,
    val subtitle: String? = null,
    val year: Int? = null,
    val poster: String? = null,
    val backdrop: String? = null,
    val logo: String? = null,
    val rating: Double? = null,
    @SerialName("progress_pct") val progressPct: Double? = null,
    @SerialName("is_favorite") val isFavorite: Boolean = false,
    val sources: List<SourceBadge> = emptyList(),
    @SerialName("now_playing") val nowPlaying: Programme? = null,
    val overview: String? = null,
    val genres: List<String> = emptyList(),
    @SerialName("runtime_min") val runtimeMin: Int? = null,
    val cast: List<CastMember> = emptyList(),
    val seasons: List<Tile> = emptyList(),
    val episodes: List<Tile> = emptyList(),
    val attribution: String? = null,
    @SerialName("request_state") val requestState: String? = null,
    val requestable: Boolean = false,
    /** Requestable series only: season numbers a request may pick from (empty = whole series only). */
    @SerialName("request_seasons") val requestSeasons: List<Int> = emptyList(),
    @SerialName("request_progress") val requestProgress: TileRequestProgress? = null,
) {
    fun asTile() = Tile(id, kind, title, subtitle, year, poster, backdrop, logo, rating, progressPct, isFavorite, sources, nowPlaying, requestState, requestable, requestProgress)
}

/**
 * GET /search: one search across titles, live channels and guide broadcasts (on now, or starting
 * within 48 hours). Every field is defaulted so the answer of a server older than a field decodes.
 */
@Serializable
data class SearchResponse(
    /** Titles, best first. Null only from servers that sent [playHere] and [openInApp] instead. */
    val results: List<Tile>? = null,
    @SerialName("metadata_available") val metadataAvailable: Boolean? = null,
    @SerialName("discovery_note") val discoveryNote: String? = null,
    @SerialName("play_here") val playHere: List<Tile> = emptyList(),
    @SerialName("open_in_app") val openInApp: List<Tile> = emptyList(),
    val channels: List<Channel> = emptyList(),
    val programmes: List<SearchProgramme> = emptyList(),
    /**
     * Ids of the results metadata discovery added: the titles the hub's own search (`discover=false`)
     * does not list. Null from a server that does not name them.
     */
    val discovered: List<String>? = null,
    val sections: SearchSections = SearchSections(),
    /** The order to show the sections in (`programmes`, `channels`, `titles`); channels first for a channel's number or whole name. */
    val order: List<String> = emptyList(),
    val attribution: String? = null,
) {
    /** The titles of the answer, whichever shape the server sent them in. */
    val titles: List<Tile> get() = results ?: (playHere + openInApp)
}

/** How many a search section found, and whether it holds more than were returned (ask again with `expand`). */
@Serializable
data class SearchSection(val count: Int = 0, val more: Boolean = false)

@Serializable
data class SearchSections(
    val titles: SearchSection = SearchSection(),
    val channels: SearchSection = SearchSection(),
    val programmes: SearchSection = SearchSection(),
)

/** One channel carrying a [SearchProgramme]. */
@Serializable
data class SearchFeed(
    @SerialName("programme_id") val programmeId: String,
    @SerialName("channel_id") val channelId: String,
    @SerialName("channel_number") val channelNumber: Int = 0,
    @SerialName("channel_name") val channelName: String = "",
    @SerialName("channel_logo") val channelLogo: String? = null,
)

/**
 * One broadcast of a search: a [Programme] as the guide lists it on the first channel carrying it
 * (allOf in the contract, flattened here), and every channel with the same title at the same start
 * as a feed. Can be recorded like any programme.
 */
@Serializable
data class SearchProgramme(
    val id: String,
    @SerialName("channel_id") val channelId: String,
    val start: String,
    val stop: String,
    val title: String,
    val description: String? = null,
    val category: String? = null,
    val icon: String? = null,
    @SerialName("catchup_available") val catchupAvailable: Boolean = false,
    @SerialName("recording_state") val recordingState: String? = null,
    @SerialName("is_sport") val isSport: Boolean = false,
    val teams: List<String> = emptyList(),
    @SerialName("on_now") val onNow: Boolean = false,
    @SerialName("channel_number") val channelNumber: Int = 0,
    @SerialName("channel_name") val channelName: String = "",
    @SerialName("channel_logo") val channelLogo: String? = null,
    val feeds: List<SearchFeed> = emptyList(),
) {
    fun asProgramme() = Programme(id, channelId, start, stop, title, description, category, icon, catchupAvailable, recordingState, isSport, teams)
}

/**
 * Query for GET /search as the TV asks it, twice for every query. [discover] false is what the hub
 * itself holds (its titles, channels and broadcasts) and answers at once; true also asks the
 * metadata provider, for titles only. [expand] names the sections wanted in full.
 */
fun searchParameters(q: String, discover: Boolean, expand: Collection<String> = emptyList()): Map<String, String> = buildMap {
    put("q", q.trim().take(200))
    expand.filter { it in SEARCH_SECTIONS }.distinct().sorted().takeIf { it.isNotEmpty() }?.let { put("expand", it.joinToString(",")) }
    if (discover) put("kinds", "movie,show") else put("discover", "false")
}

/** The section names of GET /search (`order`, `sections`, `expand`). */
val SEARCH_SECTIONS = listOf("programmes", "channels", "titles")

@Serializable
data class BrowsePage(
    val tiles: List<Tile> = emptyList(),
    val page: Int = 1,
    val total: Int = 0,
    /** Absent on servers older than the paging fields; [more] then falls back to the total. */
    @SerialName("has_more") val hasMore: Boolean? = null,
    @SerialName("page_size") val pageSize: Int? = null,
) {
    /** Whether another page follows this one. */
    val more: Boolean get() = hasMore ?: (page * (pageSize ?: DEFAULT_PAGE_SIZE) < total)

    companion object {
        /** Page size of servers that do not report one. */
        const val DEFAULT_PAGE_SIZE = 60
    }
}

// ---- Live TV -----------------------------------------------------------------------------------

@Serializable
data class Programme(
    val id: String,
    @SerialName("channel_id") val channelId: String,
    val start: String,
    val stop: String,
    val title: String,
    val description: String? = null,
    val category: String? = null,
    val icon: String? = null,
    @SerialName("catchup_available") val catchupAvailable: Boolean = false,
    /** State of the household's recording of this programme, if there is one; null also when recording is off. */
    @SerialName("recording_state") val recordingState: String? = null,
    /** The server's sports classifier says this listing is sport; the same classifier the recorder's team rules use. */
    @SerialName("is_sport") val isSport: Boolean = false,
    /**
     * The two sides, in title order, when [isSport] and the title spells out a matchup. Empty
     * otherwise: the server guesses nothing, and neither does this app.
     */
    val teams: List<String> = emptyList(),
)

@Serializable
data class Channel(
    val id: String,
    @SerialName("source_id") val sourceId: String,
    @SerialName("ext_id") val extId: String? = null,
    val number: Int,
    val name: String,
    val logo: String? = null,
    val group: String,
    val language: String = "und",
    @SerialName("language_source") val languageSource: String? = null,
    val country: String? = null,
    @SerialName("epg_id") val epgId: String? = null,
    @SerialName("catchup_days") val catchupDays: Int = 0,
    @SerialName("is_favorite") val isFavorite: Boolean = false,
    val now: Programme? = null,
    val next: Programme? = null,
)

@Serializable
data class ChannelList(
    @SerialName("list_id") val listId: String = "",
    val channels: List<Channel> = emptyList(),
    val total: Int? = null,
    val offset: Int = 0,
    val limit: Int? = null,
)

/**
 * A channel group of one source. Groups have two levels: [name] is the group's own name (for a child,
 * the part after the parent), [parent] the parent's name (null at the top level) and [path] what
 * `Channel.group` carries and `GET /channels?group=` accepts: `Parent / Child`, or the name of a
 * top-level group. A parent counts its children's channels with its own.
 */
@Serializable
data class ChannelGroup(
    val id: String,
    @SerialName("source_id") val sourceId: String,
    val name: String,
    @SerialName("channel_count") val channelCount: Int,
    val hidden: Boolean,
    @SerialName("language_override") val languageOverride: String? = null,
    val parent: String? = null,
    /** Absent on servers older than nested groups: [groupPath] then falls back to the name. */
    val path: String = "",
) {
    /** What selects this group on the wire. */
    val groupPath: String get() = path.ifBlank { name }
}

@Serializable
data class ChannelGroupPatch(
    val hidden: Boolean? = null,
    @SerialName("language_override") val languageOverride: String? = null,
    val sort: Int? = null,
)

@Serializable
data class LanguageCount(val code: String, val name: String = code, val count: Int = 0)

// ---- Playback ----------------------------------------------------------------------------------

@Serializable
data class PlaybackRequest(
    val id: String,
    @SerialName("source_id") val sourceId: String? = null,
    val client: String = "android_tv",
    @SerialName("catchup_programme_id") val catchupProgrammeId: String? = null,
    @SerialName("variant_id") val variantId: String? = null,
    @SerialName("allow_unverified_audio") val allowUnverifiedAudio: Boolean = false,
    @SerialName("rewind_at") val rewindAt: String? = null,
)

@Serializable
enum class PlaybackMode {
    @SerialName("direct") DIRECT,
    @SerialName("proxy") PROXY,
    @SerialName("hls") HLS,
    @SerialName("handoff") HANDOFF,
}

@Serializable
data class ChannelContext(
    @SerialName("list_id") val listId: String,
    val index: Int,
    val count: Int,
)

@Serializable
data class Handoff(
    @SerialName("service_key") val serviceKey: String,
    @SerialName("android_package") val androidPackage: String,
    @SerialName("android_uri") val androidUri: String? = null,
    @SerialName("android_extras") val androidExtras: Map<String, String> = emptyMap(),
    // Not in the contract (yet): explicit activity for services that need one (Hulu .WKFactivity).
    @SerialName("android_component") val androidComponent: String? = null,
    @SerialName("web_url") val webUrl: String,
    @SerialName("supports_title_link") val supportsTitleLink: Boolean = false,
)

@Serializable
data class PlaybackDescriptor(
    val id: String,
    val mode: PlaybackMode,
    val url: String? = null,
    val mime: String? = null,
    val headers: Map<String, String> = emptyMap(),
    val live: Boolean = false,
    @SerialName("resume_position_ms") val resumePositionMs: Long = 0,
    @SerialName("duration_ms") val durationMs: Long? = null,
    val title: String = "",
    val subtitle: String? = null,
    val poster: String? = null,
    @SerialName("channel_context") val channelContext: ChannelContext? = null,
    val handoff: Handoff? = null,
    @SerialName("archive_start") val archiveStart: String? = null,
    @SerialName("archive_end") val archiveEnd: String? = null,
    @SerialName("buffer_available") val bufferAvailable: Boolean = false,
    @SerialName("preferred_audio_languages") val preferredAudioLanguages: List<String> = emptyList(),
    @SerialName("audio_language_required") val audioLanguageRequired: Boolean = false,
    /**
     * Set for a stream that starts part-way into its title: media time zero of the stream is this
     * position in the title, and [durationMs] is the title's full length. On this TV that is a watch
     * room's one shared stream (see [streamEpoch]).
     */
    @SerialName("start_position_ms") val startPositionMs: Long? = null,
    /**
     * Room descriptors only, and only when the room plays one shared stream: which start of that
     * stream [startPositionMs] describes. Null for every other descriptor.
     */
    @SerialName("stream_epoch") val streamEpoch: Int? = null,
)

@Serializable
enum class PlaybackState {
    @SerialName("playing") PLAYING,
    @SerialName("paused") PAUSED,
    @SerialName("stopped") STOPPED,
}

@Serializable
data class ProgressReport(
    val id: String,
    @SerialName("expected_profile_id") val expectedProfileId: String? = null,
    @SerialName("source_id") val sourceId: String? = null,
    @SerialName("position_ms") val positionMs: Long,
    @SerialName("duration_ms") val durationMs: Long? = null,
    val state: PlaybackState,
)

// ---- Favorites / watchlist / settings ----------------------------------------------------------

@Serializable
data class Favorite(val kind: MediaKind, val id: String, val sort: Int? = null)

@Serializable
data class WatchlistPut(val id: String)

@Serializable
data class Theme(val id: String, val name: String, val builtin: Boolean = false, val tokens: JsonObject? = null)

@Serializable
data class Settings(
    @SerialName("tmdb_api_key_set") val tmdbApiKeySet: Boolean? = null,
    val region: String? = null,
    @SerialName("sleep_timer_min") val sleepTimerMin: Int? = null,
    @SerialName("epg_refresh_hours") val epgRefreshHours: Int? = null,
    @SerialName("transcode_hw") val transcodeHw: String? = null,
    @SerialName("proxy_mode") val proxyMode: String? = null,
)

/** GET /app/tv: the newest TV build the server can hand out. Every field is defaulted so a partial answer decodes. */
@Serializable
data class TvAppRelease(
    @SerialName("version_code") val versionCode: Int = 0,
    @SerialName("version_name") val versionName: String = "",
    /** Absolute, or a path on the same server. */
    @SerialName("apk_url") val apkUrl: String = "",
    /** Hex SHA-256 of the APK; the download is refused if it does not match. */
    val sha256: String = "",
)

@Serializable
data class PlaybackOption(
    @SerialName("variant_id") val variantId: String,
    @SerialName("source_id") val sourceId: String,
    @SerialName("source_name") val sourceName: String,
    @SerialName("provider_label") val providerLabel: String,
    @SerialName("audio_languages") val audioLanguages: List<String> = emptyList(),
    @SerialName("audio_verified") val audioVerified: Boolean = false,
    @SerialName("default_audio_language") val defaultAudioLanguage: String? = null,
    @SerialName("language_hint") val languageHint: String? = null,
    @SerialName("preferred_audio_available") val preferredAudioAvailable: Boolean = false,
)

@Serializable
data class PlaybackOptions(
    val id: String,
    val options: List<PlaybackOption> = emptyList(),
    @SerialName("automatic_variant_id") val automaticVariantId: String? = null,
    @SerialName("preferred_languages") val preferredLanguages: List<String> = emptyList(),
)

@Serializable data class RewindCapability(
    val mode: String = "unavailable",
    @SerialName("advertised_start") val advertisedStart: String? = null,
    @SerialName("advertised_end") val advertisedEnd: String? = null,
    val verified: Boolean = false,
    @SerialName("recording_enabled") val recordingEnabled: Boolean = false,
    @SerialName("granularity_seconds") val granularitySeconds: Int = 60,
    val reason: String = "No provider archive advertised",
)

@Serializable data class BufferWindow(
    val mode: String = "local_buffer", val start: String? = null, val end: String? = null,
    @SerialName("buffered_seconds") val bufferedSeconds: Double = 0.0,
    @SerialName("target_seconds") val targetSeconds: Int = 3600,
    @SerialName("recording_enabled") val recordingEnabled: Boolean = false,
)
@Serializable data class BufferLease(
    @SerialName("lease_id") val leaseId: String,
    @SerialName("session_id") val sessionId: String,
    val window: BufferWindow,
)
@Serializable data class BufferLeaseRequest(@SerialName("stream_token") val streamToken: String)
@Serializable data class BufferLeaseAction(
    @SerialName("stream_token") val streamToken: String,
    @SerialName("lease_id") val leaseId: String,
    val paused: Boolean = false,
    val at: String? = null,
)
@Serializable data class BufferSeekResult(val url: String, val window: BufferWindow)

@Serializable data class AuthConfig(
    @SerialName("google_enabled") val googleEnabled: Boolean = false,
    @SerialName("google_required") val googleRequired: Boolean = true,
    @SerialName("content_policy_enabled") val contentPolicyEnabled: Boolean = false,
    @SerialName("owner_pin_configured") val ownerPinConfigured: Boolean = false,
)
@Serializable data class OwnerPinStart(@SerialName("profile_id") val profileId: String, val action: String = "profile_switch")
@Serializable data class OwnerPinChallenge(
    @SerialName("challenge_id") val challengeId: String,
    @SerialName("poll_token") val pollToken: String,
    @SerialName("expires_in") val expiresIn: Int,
) {
    override fun toString(): String = "OwnerPinChallenge(challengeId=[REDACTED], pollToken=[REDACTED], expiresIn=$expiresIn)"
}
@Serializable data class OwnerPinCancel(
    @SerialName("challenge_id") val challengeId: String,
    @SerialName("poll_token") val pollToken: String,
) {
    override fun toString(): String = "OwnerPinCancel(challengeId=[REDACTED], pollToken=[REDACTED])"
}
@Serializable data class OwnerPinVerify(
    @SerialName("challenge_id") val challengeId: String,
    @SerialName("poll_token") val pollToken: String,
    val pin: String,
) {
    override fun toString(): String = "OwnerPinVerify(challengeId=[REDACTED], pollToken=[REDACTED], pin=[REDACTED])"
}
