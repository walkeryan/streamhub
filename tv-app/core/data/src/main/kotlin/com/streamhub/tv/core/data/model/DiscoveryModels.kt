package com.streamhub.tv.core.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Metadata results remain visible when their scoped availability is empty. */
@Serializable
data class DiscoveryPage(
    val tiles: List<Tile> = emptyList(),
    val page: Int = 1,
    @SerialName("page_size") val pageSize: Int = 20,
    val total: Int? = null,
    @SerialName("has_more") val hasMore: Boolean = false,
    @SerialName("coverage_note") val coverageNote: String = "Metadata coverage varies; availability is checked against your sources.",
)

@Serializable
data class NativeDiscoveryFilters(
    val source: String? = null,
    val language: String? = null,
    val year: Int? = null,
    val sort: String = "popular",
) {
    fun normalized() = copy(
        source = source?.trim()?.takeIf { it.isNotEmpty() },
        language = language?.trim()?.takeIf { it.isNotEmpty() },
        year = year?.takeIf { it in 1888..2100 },
        sort = sort.takeIf { it in listOf("popular", "rating", "newest") } ?: "popular",
    )
}

fun discoveryParameters(filters: NativeDiscoveryFilters, page: Int): Map<String, String> = buildMap {
    val value = filters.normalized()
    put("page", page.coerceIn(1, 500).toString())
    put("sort", value.sort)
    value.year?.let { put("year", it.toString()) }
    value.source?.let { put("source_id", it) }
    value.language?.let { put("language", it) }
}
