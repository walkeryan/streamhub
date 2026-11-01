package com.streamhub.tv.feature.home

import com.streamhub.tv.core.data.model.DiscoveryPage
import com.streamhub.tv.core.data.model.NativeDiscoveryFilters
import com.streamhub.tv.core.data.repo.CatalogScope
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class DiscoveryLoadTest {
    private fun query(profile: String = "one", server: String = "server", generation: String = "session", languages: List<String> = listOf("eng"), page: Int = 1, revision: Long = 1) =
        DiscoveryQuery(DiscoveryOwner(CatalogScope(server, profile, languages), generation, true), "movies", NativeDiscoveryFilters(), page, revision)
    private fun payload(page: Int) = DiscoveryPayload(DiscoveryPage(page = page, total = null, hasMore = true), emptyList(), emptyList())

    @Test fun profileServerLanguageAndSessionChangesAreDistinctScopes() {
        val original = query()
        assertNotEquals(original.owner, query(profile = "two").owner)
        assertNotEquals(original.owner, query(server = "another").owner)
        assertNotEquals(original.owner, query(generation = "new-login").owner)
        assertNotEquals(original.owner, query(languages = listOf("fra")).owner)
    }

    @Test fun scopeChangeCancelsOldRequestAndLogoutClearsResults() = runTest {
        val requests = MutableStateFlow<DiscoveryQuery?>(query())
        val pending = CompletableDeferred<DiscoveryPayload>()
        var cancelled = false
        val values = mutableListOf<DiscoveryLoad>()
        val job = backgroundScope.launch {
            discoveryLoads(requests) { request ->
                if (request.owner.catalog.profileId == "one") try { pending.await() } finally { cancelled = true }
                else payload(2)
            }.collect { values += it }
        }
        runCurrent()
        requests.value = query(profile = "two", page = 2)
        runCurrent()
        assertTrue(cancelled)
        assertEquals(2, values.last().result?.getOrNull()?.page?.page)
        pending.complete(payload(1)); runCurrent()
        assertFalse(values.any { it.result?.getOrNull()?.page?.page == 1 })
        requests.value = null; runCurrent()
        assertNull(values.last().query)
        assertNull(values.last().result)
        job.cancel()
    }

    @Test fun filterOrPageChangeCancelsOldLoadAndRetryRecoversError() = runTest {
        val requests = MutableStateFlow<DiscoveryQuery?>(query())
        val pending = CompletableDeferred<DiscoveryPayload>()
        val values = mutableListOf<DiscoveryLoad>()
        backgroundScope.launch {
            discoveryLoads(requests) { request ->
                when (request.revision) { 1L -> pending.await(); 2L -> throw IllegalStateException("Metadata unavailable"); else -> payload(request.page) }
            }.collect { values += it }
        }
        runCurrent()
        requests.value = query(page = 2, revision = 2).copy(filters = NativeDiscoveryFilters(language = "all"))
        runCurrent()
        assertTrue(values.last().result?.isFailure == true)
        requests.value = requests.value!!.copy(revision = 3)
        runCurrent()
        assertEquals(2, values.last().result?.getOrNull()?.page?.page)
        assertNull(values.last().result?.getOrNull()?.page?.total)
        assertTrue(values.last().result?.getOrNull()?.page?.hasMore == true)
        assertFalse(values.any { it.result?.exceptionOrNull() is kotlinx.coroutines.CancellationException })
    }
}
