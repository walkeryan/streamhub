package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.model.*
import kotlinx.serialization.json.Json
import kotlinx.serialization.encodeToString
import org.junit.Assert.*
import org.junit.Test

class DiscoveryAudioContractTest {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    @Test fun canonicalRankAndUnavailableArePreserved() {
        val result = json.decodeFromString<SearchResponse>("""{"results":[{"id":"tmdb:movie:11","kind":"movie","title":"Star Wars","sources":[]},{"id":"tmdb:movie:825597","kind":"movie","title":"Maggie Simpson in The Force Awakens from Its Nap","sources":[]}],"play_here":[],"open_in_app":[]} """)
        assertEquals(listOf("Star Wars", "Maggie Simpson in The Force Awakens from Its Nap"), result.results!!.map { it.title })
        assertFalse(result.results!!.first().playableInApp)
    }

    @Test fun unknownAudioDoesNotBecomeVerifiedFromProviderHint() {
        val result = json.decodeFromString<PlaybackOptions>("""{"id":"tmdb:movie:140607","options":[{"variant_id":"lib:s:1","source_id":"s","source_name":"IPTV","provider_label":"EN - The Force Awakens","language_hint":"eng","audio_verified":false}],"automatic_variant_id":null,"preferred_languages":["eng"]}""")
        assertNull(result.automaticVariantId)
        assertFalse(result.options.first().audioVerified)
        assertFalse(result.options.first().preferredAudioAvailable)
    }

    @Test fun consentIsExactAndPerRequest() {
        val initial = PlaybackRequest("tmdb:movie:140607")
        val consent = initial.copy(variantId = "lib:s:1", allowUnverifiedAudio = true)
        assertFalse(initial.allowUnverifiedAudio)
        assertTrue(json.encodeToString(consent).contains("\"variant_id\":\"lib:s:1\""))
        assertTrue(json.encodeToString(consent).contains("\"allow_unverified_audio\":true"))
        assertFalse(PlaybackRequest("tmdb:movie:11").allowUnverifiedAudio)
    }
}
