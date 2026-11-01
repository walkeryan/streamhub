package com.streamhub.tv.feature.search

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SearchLoadsTest {
    @Test fun profileServerAndLanguageChangesCancelAndClearPreviousResults() = runTest {
        val initial = SearchRequest(SearchScope("server-one", "one", listOf("eng")), "movie", null)
        val requests = MutableStateFlow<SearchRequest?>(initial)
        val values = mutableListOf<SearchLoad<String>>()
        val pending = CompletableDeferred<String>()
        var cancelled = false
        backgroundScope.launch {
            searchLoads(requests) { request ->
                if (request == initial) try { pending.await() } finally { cancelled = true }
                else "${request.scope.server}/${request.scope.profileId}/${request.language}"
            }.collect { values += it }
        }
        runCurrent(); advanceTimeBy(400); runCurrent()
        requests.value = initial.copy(scope = initial.scope.copy(profileId = "two"))
        runCurrent()
        assertTrue(cancelled)
        assertTrue(values.last().loading)
        assertNull(values.last().result)
        advanceTimeBy(400); runCurrent()
        assertEquals("server-one/two/null", values.last().result?.getOrNull())
        requests.value = requests.value!!.copy(scope = initial.scope.copy(server = "server-two", profileId = "two"), language = "und")
        runCurrent()
        assertNull(values.last().result)
        advanceTimeBy(400); runCurrent()
        assertEquals("server-two/two/und", values.last().result?.getOrNull())
        pending.complete("old profile result"); runCurrent()
        assertFalse(values.any { it.result?.isFailure == true || it.result?.getOrNull() == "old profile result" })
        requests.value = null; runCurrent()
        assertNull(values.last().result)
    }

    @Test fun changingProfileLanguageAndShorteningQueryInvalidateResults() = runTest {
        val initial = SearchRequest(SearchScope("server", "one", listOf("eng")), "title", null)
        val requests = MutableStateFlow<SearchRequest?>(initial)
        val values = mutableListOf<SearchLoad<String>>()
        var calls = 0
        backgroundScope.launch { searchLoads(requests) { calls++; it.scope.preferredLanguages.joinToString() }.collect { values += it } }
        runCurrent(); advanceTimeBy(400); runCurrent()
        requests.value = initial.copy(scope = initial.scope.copy(preferredLanguages = listOf("spa")))
        runCurrent(); assertNull(values.last().result)
        advanceTimeBy(400); runCurrent()
        assertEquals("spa", values.last().result?.getOrNull())
        requests.value = requests.value!!.copy(query = "t")
        runCurrent(); advanceTimeBy(500); runCurrent()
        assertNull(values.last().result)
        assertFalse(values.last().loading)
        assertEquals(2, calls)
    }
}
