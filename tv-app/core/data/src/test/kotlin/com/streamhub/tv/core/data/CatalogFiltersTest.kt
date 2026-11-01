package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.mergeProfileFilter
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class CatalogFiltersTest {
    @Test fun nativeFilterSavePreservesOtherScreensAndWebPreferences() {
        val existing = StreamHubJson.parseToJsonElement("""{"web_filters":{"region":"US"},"native_browse_shows":{"genre":"drama"},"native_browse_movies":{"sort":"year"}}""").jsonObject
        val movies = StreamHubJson.encodeToJsonElement(NativeBrowseFilters(source = "source-a", genre = "horror", language = "all"))
        val merged = mergeProfileFilter(existing, "native_browse_movies", movies)
        assertEquals(existing["web_filters"], merged["web_filters"])
        assertEquals(existing["native_browse_shows"], merged["native_browse_shows"])
        assertEquals("all", merged["native_browse_movies"]!!.jsonObject["language"]!!.jsonPrimitive.content)
        assertEquals("year", existing["native_browse_movies"]!!.jsonObject["sort"]!!.jsonPrimitive.content)
    }
    @Test fun missingPreferenceUsesProfileLanguagesWhileAllAndUnknownRemainExplicit() {
        assertNull(StreamHubJson.decodeFromString(NativeBrowseFilters.serializer(), "{}").language)
        for (language in listOf("all", "und", "fra")) {
            val saved = NativeBrowseFilters(language = language)
            assertEquals(saved, StreamHubJson.decodeFromString(NativeBrowseFilters.serializer(), StreamHubJson.encodeToString(NativeBrowseFilters.serializer(), saved)))
        }
    }
    @Test fun sportsWatchUsesActualChannelIdAndRetainsCoverage() {
        val page = StreamHubJson.decodeFromString(SportsPage.serializer(), """{"items":[{"id":"4:12","sport":"Tennis","title":"Final replay","channel_id":"ch:provider:44","channel_name":"Sports One","source_name":"Provider","start":"2026-11-01T10:00:00Z","stop":"2026-11-01T12:00:00Z","extra":"ignored"}],"total":81,"sports":[{"name":"Tennis","count":81}],"coverage":{"eligible_channels":1000,"channels_with_current_schedule":85}}""")
        assertEquals("ch:provider:44", page.items.single().channelId)
        assertEquals(81, page.total)
        assertEquals(85, page.coverage.scheduledChannels)
        assertEquals(1, page.items.size)
    }
}
