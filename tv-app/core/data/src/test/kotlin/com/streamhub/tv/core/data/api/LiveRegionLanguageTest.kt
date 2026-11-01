package com.streamhub.tv.core.data.api

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.get
import io.ktor.http.HttpStatusCode
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class LiveRegionLanguageTest {
    @Test fun usAndEnglishAreIndependentRequests() = runTest {
        val expected = listOf(
            "US" to "eng", "US" to "spa", "all" to "eng", "und" to "all", null to null,
        )
        var index = 0
        val client = HttpClient(MockEngine { request ->
            val (country, language) = expected[index++]
            assertEquals(country, request.url.parameters["country"])
            assertEquals(language, request.url.parameters["language"])
            respond("{}", HttpStatusCode.OK)
        })
        try {
            client.get("https://fixture.invalid/channels") { applyLiveRegionLanguage(listOf("eng"), "US") }
            client.get("https://fixture.invalid/channels") { applyLiveRegionLanguage(listOf("spa"), "US") }
            client.get("https://fixture.invalid/channels/groups") { applyLiveRegionLanguage(listOf("eng"), "all") }
            client.get("https://fixture.invalid/channels") { applyLiveRegionLanguage(emptyList(), "und") }
            client.get("https://fixture.invalid/channels") { applyLiveRegionLanguage(null, null) }
            assertEquals(expected.size, index)
        } finally { client.close() }
    }
}
