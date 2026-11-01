package com.streamhub.tv.core.data.api

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.get
import io.ktor.http.HttpStatusCode
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class SearchFiltersTest {
    @Test fun profileAllUnknownAndCsvKeepSourceScope() = runTest {
        val selections = listOf(null, emptyList(), listOf("und"), listOf("eng", "spa"))
        val expected = listOf(null, "all", "und", "eng,spa")
        var index = 0
        val client = HttpClient(MockEngine { request ->
            assertEquals(expected[index++], request.url.parameters["language"])
            assertEquals("source-one", request.url.parameters["source_id"])
            respond("{}", HttpStatusCode.OK)
        })
        try {
            selections.forEach { languages -> client.get("https://fixture.invalid/search") { applySearchFilters(languages, "source-one") } }
            assertEquals(4, index)
        } finally { client.close() }
    }
}
