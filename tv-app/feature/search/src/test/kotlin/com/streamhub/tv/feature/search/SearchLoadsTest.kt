package com.streamhub.tv.feature.search

import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.SearchProgramme
import com.streamhub.tv.core.data.model.SearchResponse
import com.streamhub.tv.core.data.model.SearchSection
import com.streamhub.tv.core.data.model.SearchSections
import com.streamhub.tv.core.data.model.Tile
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SearchLoadsTest {
    private val scope = SearchScope("server-one", "one", listOf("eng"))
    private fun title(id: String) = Tile(id, MediaKind.MOVIE, "Title $id")
    private fun channel(id: String, number: Int = 1, name: String = "Channel $id") = Channel(id, "s", number = number, name = name, group = "g")
    private fun broadcast(id: String) = SearchProgramme(id, "ch:1", "2026-11-13T20:00:00Z", "2026-11-13T21:00:00Z", "Broadcast $id")
    private fun answer(titles: List<String> = emptyList(), channels: List<String> = emptyList(), programmes: List<String> = emptyList(), discovered: List<String>? = null, order: List<String> = emptyList(), more: SearchSections = SearchSections()) =
        SearchResponse(results = titles.map(::title), channels = channels.map { channel(it) }, programmes = programmes.map(::broadcast), discovered = discovered, order = order, sections = more)

    @Test fun profileAndServerChangesCancelAndClearPreviousResults() = runTest {
        val initial = SearchRequest(scope, "movie")
        val requests = MutableStateFlow<SearchRequest?>(initial)
        val values = mutableListOf<SearchLoad>()
        val pending = CompletableDeferred<SearchResponse>()
        var cancelled = false
        backgroundScope.launch {
            searchLoads(requests, local = { request ->
                if (request == initial) try { pending.await() } finally { cancelled = true }
                else answer(titles = listOf("${request.scope.server}/${request.scope.profileId}"))
            }, full = { answer() }).collect { values += it }
        }
        runCurrent(); advanceTimeBy(400); runCurrent()
        requests.value = initial.copy(scope = scope.copy(profileId = "two"))
        runCurrent()
        assertTrue(cancelled)
        assertTrue(values.last().loading)
        assertNull(values.last().view)
        advanceTimeBy(400); runCurrent()
        assertEquals(listOf("server-one/two"), values.last().view?.titles?.map { it.id })
        requests.value = requests.value!!.copy(scope = scope.copy(server = "server-two", profileId = "two"))
        runCurrent()
        assertNull(values.last().view)
        advanceTimeBy(400); runCurrent()
        assertEquals(listOf("server-two/two"), values.last().view?.titles?.map { it.id })
        pending.complete(answer(titles = listOf("old profile result"))); runCurrent()
        assertFalse(values.any { it.error != null || it.view?.titles?.any { t -> t.id == "old profile result" } == true })
        requests.value = null; runCurrent()
        assertNull(values.last().view)
    }

    @Test fun changingProfileLanguageAndShorteningQueryInvalidateResults() = runTest {
        val initial = SearchRequest(scope, "title")
        val requests = MutableStateFlow<SearchRequest?>(initial)
        val values = mutableListOf<SearchLoad>()
        var calls = 0
        backgroundScope.launch { searchLoads(requests, local = { calls++; answer(titles = it.scope.preferredLanguages) }, full = { answer() }).collect { values += it } }
        runCurrent(); advanceTimeBy(400); runCurrent()
        requests.value = initial.copy(scope = scope.copy(preferredLanguages = listOf("spa")))
        runCurrent(); assertNull(values.last().view)
        advanceTimeBy(400); runCurrent()
        assertEquals(listOf("spa"), values.last().view?.titles?.map { it.id })
        requests.value = requests.value!!.copy(query = "t")
        runCurrent(); advanceTimeBy(500); runCurrent()
        assertNull(values.last().view)
        assertFalse(values.last().loading)
        assertEquals(2, calls)
    }

    @Test fun theHubsAnswerIsShownFirstAndDiscoveredTitlesJoinTheEndWithoutReordering() = runTest {
        val requests = MutableStateFlow<SearchRequest?>(SearchRequest(scope, "mill"))
        val values = mutableListOf<SearchLoad>()
        val provider = CompletableDeferred<SearchResponse>()
        backgroundScope.launch {
            searchLoads(requests,
                local = { answer(titles = listOf("lib:1", "lib:2"), channels = listOf("c1"), programmes = listOf("p1"), order = listOf("programmes", "channels", "titles")) },
                full = { provider.await() },
            ).collect { values += it }
        }
        runCurrent(); advanceTimeBy(400); runCurrent()
        val first = values.last()
        assertEquals(listOf("lib:1", "lib:2"), first.view!!.titles.map { it.id })
        assertTrue("more titles may still come", first.discovering)
        assertFalse(first.loading)
        // The provider ranks its own way and repeats the library's titles: what is shown keeps its place.
        provider.complete(answer(titles = listOf("tmdb:9", "lib:2", "tmdb:7", "lib:1", "lib:3"), discovered = listOf("tmdb:9", "tmdb:7")))
        runCurrent()
        val merged = values.last()
        assertEquals(listOf("lib:1", "lib:2", "tmdb:9", "tmdb:7"), merged.view!!.titles.map { it.id })
        assertEquals(first.view!!.channels, merged.view!!.channels)
        assertEquals(first.view!!.programmes, merged.view!!.programmes)
        assertEquals(first.view!!.order, merged.view!!.order)
        assertFalse(merged.discovering)
        assertNull(merged.error)
    }

    @Test fun aProviderThatAnswersFirstWaitsForTheHub() = runTest {
        val requests = MutableStateFlow<SearchRequest?>(SearchRequest(scope, "mill"))
        val values = mutableListOf<SearchLoad>()
        val hub = CompletableDeferred<SearchResponse>()
        backgroundScope.launch { searchLoads(requests, local = { hub.await() }, full = { answer(titles = listOf("tmdb:9", "lib:1")) }).collect { values += it } }
        runCurrent(); advanceTimeBy(400); runCurrent()
        assertNull("nothing is shown before the hub's own answer", values.last().view)
        hub.complete(answer(titles = listOf("lib:1")))
        runCurrent()
        // A server that names no discovered titles: the ones not shown yet are the added ones.
        assertEquals(listOf("lib:1", "tmdb:9"), values.last().view!!.titles.map { it.id })
        assertEquals(listOf(listOf("lib:1"), listOf("lib:1", "tmdb:9")), values.mapNotNull { it.view?.titles?.map { t -> t.id } })
    }

    @Test fun showMoreKeepsWhatIsShownAndAddsRowsAtTheEnd() = runTest {
        val capped = SearchRequest(scope, "news")
        val requests = MutableStateFlow<SearchRequest?>(capped)
        val values = mutableListOf<SearchLoad>()
        backgroundScope.launch {
            searchLoads(requests, local = { request ->
                if (request.expand.isEmpty()) answer(titles = listOf("lib:1"), channels = listOf("c1", "c2"), more = SearchSections(channels = SearchSection(5, true)), order = listOf("channels", "programmes", "titles"))
                else answer(titles = listOf("lib:1"), channels = listOf("c1", "c2", "c3", "c4", "c5"), more = SearchSections(channels = SearchSection(5, false)), order = listOf("programmes", "channels", "titles"))
            }, full = { answer(titles = listOf("lib:1", "tmdb:5"), discovered = listOf("tmdb:5")) }).collect { values += it }
        }
        runCurrent(); advanceTimeBy(400); runCurrent()
        val before = values.last().view!!
        assertEquals(setOf(SearchSectionName.CHANNELS), before.more)
        assertEquals(listOf("lib:1", "tmdb:5"), before.titles.map { it.id })
        values.clear()
        requests.value = capped.copy(expand = setOf(SearchSectionName.CHANNELS))
        runCurrent()
        // No blank page and no debounce: the rows stay up while the rest is fetched.
        assertTrue(values.first().growing)
        assertEquals(before, values.first().view)
        assertTrue(values.none { it.view == null || it.loading })
        val after = values.last().view!!
        assertEquals(listOf("c1", "c2", "c3", "c4", "c5"), after.channels.map { it.id })
        assertEquals(listOf("lib:1", "tmdb:5"), after.titles.map { it.id })
        assertEquals("the order on screen is not changed by a later answer", before.order, after.order)
        assertTrue(after.more.isEmpty())
        assertFalse(values.last().growing)
    }

    @Test fun withoutTheHubsAnswerTheFullOneIsShownAndWithNeitherTheSearchFails() = runTest {
        val requests = MutableStateFlow<SearchRequest?>(SearchRequest(scope, "mill"))
        val values = mutableListOf<SearchLoad>()
        var providerFails = false
        backgroundScope.launch {
            searchLoads(requests, local = { error("hub down") }, full = { if (providerFails) error("provider down") else answer(titles = listOf("lib:1", "tmdb:9"), discovered = listOf("tmdb:9")) }).collect { values += it }
        }
        runCurrent(); advanceTimeBy(400); runCurrent()
        assertEquals(listOf("lib:1", "tmdb:9"), values.last().view!!.titles.map { it.id })
        assertNull(values.last().error)
        providerFails = true
        requests.value = SearchRequest(scope, "other")
        runCurrent(); advanceTimeBy(400); runCurrent()
        assertNull(values.last().view)
        assertEquals("hub down", values.last().error?.message)
        assertFalse(values.last().loading)
    }

    @Test fun aFailedProviderLeavesTheHubsResultsStanding() = runTest {
        val requests = MutableStateFlow<SearchRequest?>(SearchRequest(scope, "mill"))
        val values = mutableListOf<SearchLoad>()
        backgroundScope.launch { searchLoads(requests, local = { answer(titles = listOf("lib:1")) }, full = { error("provider down") }).collect { values += it } }
        runCurrent(); advanceTimeBy(400); runCurrent()
        assertEquals(listOf("lib:1"), values.last().view!!.titles.map { it.id })
        assertNull(values.last().error)
        assertFalse(values.last().discovering)
    }

    @Test fun anOlderQuerysLateAnswerIsDropped() = runTest {
        val requests = MutableStateFlow<SearchRequest?>(SearchRequest(scope, "first"))
        val values = mutableListOf<SearchLoad>()
        backgroundScope.launch {
            searchLoads(requests, local = { answer(titles = listOf("hub:${it.query}")) }, full = { request ->
                if (request.query == "first") awaitCancellation() else answer(titles = listOf("tmdb:${request.query}"))
            }).collect { values += it }
        }
        runCurrent(); advanceTimeBy(400); runCurrent()
        assertEquals(listOf("hub:first"), values.last().view!!.titles.map { it.id })
        requests.value = SearchRequest(scope, "second")
        runCurrent(); advanceTimeBy(400); runCurrent()
        assertEquals(listOf("hub:second", "tmdb:second"), values.last().view!!.titles.map { it.id })
        assertEquals("second", values.last().request?.query)
    }

    @Test fun rowsKeepTheirPlacesAndAreRefreshedInThem() {
        val shown = listOf(title("a"), title("b"))
        val incoming = listOf(title("c"), title("b").copy(requestState = "sent"), title("c"), title("a"))
        val merged = appendUnseen(shown, incoming) { it.id }
        assertEquals(listOf("a", "b", "c"), merged.map { it.id })
        assertEquals("sent", merged[1].requestState)
        assertEquals(emptyList<Tile>(), appendUnseen(emptyList(), emptyList<Tile>()) { it.id })
    }

    @Test fun theServersOrderIsFollowedAndAnExactChannelPutsChannelsFirstWithoutOne() {
        val p = SearchSectionName.PROGRAMMES; val c = SearchSectionName.CHANNELS; val t = SearchSectionName.TITLES
        assertEquals(listOf(p, c, t), searchOrder(emptyList(), "news", listOf(channel("1", 5, "World News"))))
        assertEquals(listOf(c, p, t), searchOrder(listOf("channels", "programmes", "titles"), "news", emptyList()))
        // An unknown name is skipped and a missing section still has its place.
        assertEquals(listOf(c, p, t), searchOrder(listOf("channels", "people"), "x", emptyList()))
        assertEquals(listOf(c, p, t), searchOrder(emptyList(), " world news ", listOf(channel("1", 5, "World News"))))
        assertEquals(listOf(c, p, t), searchOrder(emptyList(), "5", listOf(channel("1", 5, "World News"))))
        assertEquals(listOf(p, c, t), searchOrder(emptyList(), "50", listOf(channel("1", 5, "World News"))))
        assertEquals(listOf("On TV", "Channels", "Movies & Shows"), SearchSectionName.entries.map { it.heading })
    }

    @Test fun theFirstLocalAnswerSetsTheOrderAndLaterOnesDoNot() {
        val first = SearchView().withLocal(answer(channels = listOf("c1"), order = listOf("channels", "programmes", "titles")), "c1", first = true)
        assertEquals(SearchSectionName.CHANNELS, first.order.first())
        val later = first.withLocal(answer(channels = listOf("c1", "c2"), order = listOf("programmes", "channels", "titles")), "c1", first = false)
        assertEquals(first.order, later.order)
        assertEquals(3, first.withFull(answer(titles = listOf("a", "b", "c"))).found - first.found)
    }
}
