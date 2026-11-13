package com.streamhub.tv.core.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable data class CatalogFacet(val name: String, val count: Int = 0)
/** [category] is the provider's own grouping (a shelf on Movies/Shows); it combines with [genre]. */
@Serializable data class NativeBrowseFilters(val source: String? = null, val genre: String? = null, val language: String? = null, val sort: String = "added", val category: String? = null)

/**
 * Query for GET /browse/{section}. Absent values are left out so the server applies its own defaults
 * (profile languages, "added" order); a blank search is not a search.
 */
fun browseParameters(
    sourceId: String? = null, genre: String? = null, sort: String? = null, page: Int = 1,
    language: String? = null, category: String? = null, q: String? = null,
): Map<String, String> = buildMap {
    sourceId?.let { put("source_id", it) }
    genre?.let { put("genre", it) }
    sort?.let { put("sort", it) }
    put("page", page.coerceAtLeast(1).toString())
    category?.let { put("category", it) }
    language?.let { put("language", it) }
    q?.trim()?.take(200)?.takeIf { it.isNotEmpty() }?.let { put("q", it) }
}

/** Query for the facet lists (/browse/{section}/categories and /genres): the scope their counts are taken in. */
fun browseFacetParameters(sourceId: String? = null, language: String? = null, category: String? = null, q: String? = null): Map<String, String> = buildMap {
    sourceId?.let { put("source_id", it) }
    language?.let { put("language", it) }
    category?.let { put("category", it) }
    q?.trim()?.take(200)?.takeIf { it.isNotEmpty() }?.let { put("q", it) }
}
@Serializable data class NativeSportsFilters(val language: String? = null, val sport: String? = null, val date: String? = null, val status: String = "all", val query: String = "")
@Serializable data class SportsListing(
    val id: String, val sport: String, val title: String,
    @SerialName("channel_id") val channelId: String,
    @SerialName("channel_name") val channelName: String,
    @SerialName("source_name") val sourceName: String,
    val start: String, val stop: String,
)
@Serializable data class SportsCoverage(
    @SerialName("eligible_channels") val eligibleChannels: Int = 0,
    @SerialName("channels_with_current_schedule") val scheduledChannels: Int = 0,
)
@Serializable data class SportsPage(
    val items: List<SportsListing> = emptyList(), val total: Int = 0,
    val sports: List<CatalogFacet> = emptyList(), val coverage: SportsCoverage = SportsCoverage(),
)

@Serializable data class LiveFilters(val languages: Set<String>? = null, val group: String? = null, val country: String = "US", val favorites: Boolean = false)

@Serializable data class SportsFeed(
    @SerialName("programme_id") val programmeId: String,
    @SerialName("channel_id") val channelId: String,
    @SerialName("channel_name") val channelName: String,
    @SerialName("source_id") val sourceId: String,
    @SerialName("source_name") val sourceName: String,
    val language: String? = null,
    @SerialName("language_verified") val languageVerified: Boolean = false,
)
@Serializable data class SportsEvent(
    val id: String, val title: String, val sport: String,
    val start: String, val stop: String, val status: String,
    val replay: Boolean = false,
    @SerialName("live_evidence") val liveEvidence: Boolean = false,
    @SerialName("grouping_evidence") val groupingEvidence: String? = null,
    val feeds: List<SportsFeed> = emptyList(),
)
@Serializable data class SportsEventCoverage(
    @SerialName("eligible_channels") val eligibleChannels: Int = 0,
    @SerialName("channels_with_sports_schedule") val scheduledChannels: Int = 0,
)
@Serializable data class SportsEventsPage(
    @SerialName("as_of") val asOf: String? = null,
    val date: String? = null, val timezone: String = "America/New_York",
    val events: List<SportsEvent> = emptyList(), val total: Int = 0,
    val sports: List<CatalogFacet> = emptyList(),
    val coverage: SportsEventCoverage = SportsEventCoverage(), val truncated: Boolean = false,
)

@Serializable data class SportsRecording(
    @SerialName("title_id") val titleId: String,
    val title: String,
    @SerialName("source_id") val sourceId: String,
    @SerialName("source_name") val sourceName: String,
    val sport: String,
    @SerialName("originally_available_at") val originallyAvailableAt: String? = null,
    @SerialName("duration_ms") val durationMs: Long? = null,
    @SerialName("audio_languages") val audioLanguages: List<String> = emptyList(),
    @SerialName("audio_verified") val audioVerified: Boolean = false,
    @SerialName("language_hint") val languageHint: String? = null,
    val kind: String = "library_video", val live: Boolean = false,
)
@Serializable data class SportsRecordingsPage(
    val items: List<SportsRecording> = emptyList(), val total: Int = 0,
    val limit: Int = 40, val offset: Int = 0, val truncated: Boolean = false,
)
/** Guide date/status are deliberately absent: library videos are not event feeds. */
fun sportsRecordingParameters(filters: NativeSportsFilters, offset: Int, limit: Int = 40): Map<String,String> = buildMap {
    filters.language?.let { put("language",it) }
    filters.sport?.let { put("sport",it) }
    filters.query.trim().take(200).takeIf { it.isNotEmpty() }?.let { put("q",it) }
    put("offset",offset.coerceAtLeast(0).toString()); put("limit",limit.coerceIn(1,100).toString())
}
