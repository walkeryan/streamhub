package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.*
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AudioPlaybackAttemptTest {
    private val choice = PlaybackOptions("tmdb:movie:11", listOf(PlaybackOption("lib:s:1", "s", "IPTV", "EN - Star Wars")))

    @Test fun choosingDoesNotResolveOrAutoplay() = runTest {
        var resolves = 0
        val result = audioPlaybackAttempt(PlaybackRequest(choice.id), true, { _, _ -> choice }, { resolves++; error("unexpected resolve") })
        assertTrue(result is AudioPlaybackAttempt.Choice)
        assertEquals(0, resolves)
        assertFalse((result as AudioPlaybackAttempt.Choice).request.allowUnverifiedAudio)
    }

    @Test fun cancelOrProfileChangeSuppressesLateChoice() = runTest {
        var delivered = false
        var resolves = 0
        val job = launch {
            audioPlaybackAttempt(PlaybackRequest(choice.id), true, { _, _ ->
                withContext(NonCancellable) { delay(100) }; choice
            }, { resolves++; error("unexpected resolve") })
            delivered = true
        }
        runCurrent(); job.cancel(); advanceUntilIdle()
        assertFalse(delivered)
        assertEquals(0, resolves)
    }

    @Test fun exactConsentIsSubmittedOnceAndNewRequestHasNone() = runTest {
        val requests = mutableListOf<PlaybackRequest>()
        val consent = PlaybackRequest(choice.id, variantId = "lib:s:1", allowUnverifiedAudio = true)
        try {
            audioPlaybackAttempt(consent, false, { _, _ -> error("must not reselect") }, { requests += it; error("fixture stops before media") })
        } catch (_: IllegalStateException) { }
        assertEquals(listOf(consent), requests)
        val fresh = audioPlaybackAttempt(PlaybackRequest("tmdb:movie:12"), true, { _, _ -> choice }, { error("must ask") }) as AudioPlaybackAttempt.Choice
        assertNull(fresh.request.variantId)
        assertFalse(fresh.request.allowUnverifiedAudio)
    }
    @Test fun liveChannelsKeepDirectPath() = runTest {
        var resolves = 0
        try {
            audioPlaybackAttempt(PlaybackRequest("ch:s:7"), true, { _, _ -> error("live must not load VOD options") }, { resolves++; error("fixture ends") })
        } catch (_: IllegalStateException) { }
        assertEquals(1, resolves)
    }

    @Test fun verifiedAutomaticVariantResolvesWithoutConsentOrChooser() = runTest {
        val verified = choice.copy(options = choice.options.map { it.copy(audioVerified = true, preferredAudioAvailable = true) }, automaticVariantId = "lib:s:1", preferredLanguages = listOf("eng"))
        val requests = mutableListOf<PlaybackRequest>()
        val result = audioPlaybackAttempt(PlaybackRequest(choice.id), true, { _, _ -> verified }, { requests += it; PlaybackDescriptor(it.id, PlaybackMode.PROXY) })
        assertTrue(result is AudioPlaybackAttempt.Ready)
        assertEquals(1, requests.size)
        assertEquals("lib:s:1", requests.single().variantId)
        assertFalse(requests.single().allowUnverifiedAudio)
    }

}
