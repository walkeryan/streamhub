package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.DiscoveryPage
import com.streamhub.tv.core.data.model.NativeDiscoveryFilters
import com.streamhub.tv.core.data.model.discoveryParameters
import org.junit.Assert.*
import org.junit.Test

class DiscoveryPageTest {
    @Test fun restrictedSparsePagesKeepUnknownTotalAndContinuation() {
        val page = StreamHubJson.decodeFromString<DiscoveryPage>("""{"tiles":[],"page":2,"page_size":20,"total":null,"has_more":true,"coverage_note":"Permitted classifications only."}""")
        assertNull(page.total)
        assertTrue(page.hasMore)
        assertEquals(20, page.pageSize)
        assertEquals("Permitted classifications only.", page.coverageNote)
    }

    @Test fun unavailableMetadataRemainsInCanonicalOrder() {
        val page = StreamHubJson.decodeFromString<DiscoveryPage>("""{"tiles":[{"id":"tmdb:movie:11","kind":"movie","title":"Star Wars","sources":[]},{"id":"tmdb:movie:12","kind":"movie","title":"Other","sources":[]}],"page":1,"page_size":20,"total":2,"has_more":false,"coverage_note":"Metadata catalog."}""")
        assertEquals(listOf("tmdb:movie:11", "tmdb:movie:12"), page.tiles.map { it.id })
        assertFalse(page.tiles.first().playableInApp)
    }

    @Test fun availabilityFiltersAndMetadataControlsHaveDistinctParameters() {
        val values = discoveryParameters(NativeDiscoveryFilters(source = "iptv", language = "eng", year = 2024, sort = "rating"), 3)
        assertEquals(mapOf("source_id" to "iptv", "language" to "eng", "year" to "2024", "sort" to "rating", "page" to "3"), values)
        assertFalse(values.containsKey("page_size"))
        assertFalse(values.containsKey("original_language"))
        assertEquals(mapOf("sort" to "popular", "page" to "1"), discoveryParameters(NativeDiscoveryFilters(), 0))
        assertEquals("all", discoveryParameters(NativeDiscoveryFilters(language = "all"), 1)["language"])
        assertEquals("500", discoveryParameters(NativeDiscoveryFilters(sort = "invalid", year = 2101), 501)["page"])
        assertNull(discoveryParameters(NativeDiscoveryFilters(year = 2101), 1)["year"])
    }
}
