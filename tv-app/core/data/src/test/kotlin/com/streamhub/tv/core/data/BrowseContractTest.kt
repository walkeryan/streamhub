package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.BrowsePage
import com.streamhub.tv.core.data.model.CatalogFacet
import com.streamhub.tv.core.data.model.NativeBrowseFilters
import com.streamhub.tv.core.data.model.browseFacetParameters
import com.streamhub.tv.core.data.model.browseParameters
import kotlinx.serialization.builtins.ListSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Shapes from contracts/openapi.yaml: /browse/{section}, /browse/{section}/categories and /genres. */
class BrowseContractTest {
    @Test fun browsePageCarriesPagingFields() {
        val page = StreamHubJson.decodeFromString(BrowsePage.serializer(),
            """{"tiles":[{"id":"lib:1","kind":"movie","title":"One","sources":[]}],"page":2,"total":95,"has_more":true,"page_size":40,"later_field":1}""")
        assertEquals(listOf("lib:1"), page.tiles.map { it.id })
        assertEquals(2, page.page)
        assertEquals(95, page.total)
        assertEquals(true, page.hasMore)
        assertEquals(40, page.pageSize)
        assertTrue(page.more)
    }

    @Test fun serverSaysWhenThereIsNoMoreEvenIfTheTotalSuggestsOtherwise() {
        val page = StreamHubJson.decodeFromString(BrowsePage.serializer(), """{"tiles":[],"page":1,"total":500,"has_more":false,"page_size":20}""")
        assertFalse(page.more)
    }

    @Test fun olderServersWithoutPagingFieldsFallBackToTheTotal() {
        val first = StreamHubJson.decodeFromString(BrowsePage.serializer(), """{"tiles":[],"page":1,"total":61}""")
        assertNull(first.hasMore)
        assertNull(first.pageSize)
        assertTrue(first.more)
        assertFalse(StreamHubJson.decodeFromString(BrowsePage.serializer(), """{"tiles":[],"page":2,"total":61}""").more)
        assertFalse(StreamHubJson.decodeFromString(BrowsePage.serializer(), "{}").more)
        // A reported page size is used when has_more is missing.
        assertTrue(StreamHubJson.decodeFromString(BrowsePage.serializer(), """{"page":1,"total":21,"page_size":20}""").more)
    }

    @Test fun categoriesDecodeInServerOrder() {
        val categories = StreamHubJson.decodeFromString(ListSerializer(CatalogFacet.serializer()),
            """[{"name":"New Releases","count":120},{"name":"Action","count":48,"icon":"ignored"},{"name":"Uncounted"}]""")
        assertEquals(listOf("New Releases", "Action", "Uncounted"), categories.map { it.name })
        assertEquals(listOf(120, 48, 0), categories.map { it.count })
        assertEquals(emptyList<CatalogFacet>(), StreamHubJson.decodeFromString(ListSerializer(CatalogFacet.serializer()), "[]"))
    }

    @Test fun browseQueryCarriesCategoryAndSearch() {
        assertEquals(
            mapOf("source_id" to "source-a", "genre" to "Drama", "sort" to "year", "page" to "3", "category" to "New Releases", "language" to "eng", "q" to "night"),
            browseParameters("source-a", "Drama", "year", 3, "eng", "New Releases", "  night "),
        )
    }

    @Test fun browseQueryOmitsWhatIsNotSet() {
        assertEquals(mapOf("page" to "1"), browseParameters())
        assertEquals(mapOf("page" to "1"), browseParameters(page = 0, q = "   "))
        assertEquals(mapOf("page" to "1", "category" to "Kids & Family / 4K"), browseParameters(category = "Kids & Family / 4K"))
        assertEquals(200, browseParameters(q = "x".repeat(500)).getValue("q").length)
    }

    @Test fun facetQueriesShareTheBrowseScope() {
        // /categories takes source_id, language and q; /genres additionally narrows by category.
        assertEquals(mapOf("source_id" to "source-a", "language" to "all", "q" to "night"), browseFacetParameters("source-a", "all", q = "night"))
        assertEquals(mapOf("language" to "und", "category" to "Action"), browseFacetParameters(language = "und", category = "Action"))
        assertEquals(emptyMap<String, String>(), browseFacetParameters())
    }

    @Test fun savedFiltersRoundTripWithAndWithoutACategory() {
        val saved = NativeBrowseFilters(source = "source-a", genre = "Drama", language = "all", sort = "title", category = "Action")
        assertEquals(saved, StreamHubJson.decodeFromString(NativeBrowseFilters.serializer(), StreamHubJson.encodeToString(NativeBrowseFilters.serializer(), saved)))
        // Preferences written before categories existed.
        assertNull(StreamHubJson.decodeFromString(NativeBrowseFilters.serializer(), """{"source":"source-a","sort":"year"}""").category)
    }
}
