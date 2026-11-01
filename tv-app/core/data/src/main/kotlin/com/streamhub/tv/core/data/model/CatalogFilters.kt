package com.streamhub.tv.core.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable data class CatalogFacet(val name: String, val count: Int = 0)
@Serializable data class NativeBrowseFilters(val source: String? = null, val genre: String? = null, val language: String? = null, val sort: String = "added")
@Serializable data class NativeSportsFilters(val language: String? = null, val sport: String? = null)
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
