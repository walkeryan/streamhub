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
    @Test fun liveUnknownAudioPlaysTheChannelWithoutAChoice() = runTest {
        // Today's server: a provider hint only, no automatic variant. The channel still plays, consent sent explicitly.
        val order=mutableListOf<String>()
        val requests=mutableListOf<PlaybackRequest>()
        val live=choice.copy(id="ch:s:7", options=choice.options.map { it.copy(variantId="ch:s:7", languageHint="eng") }, preferredLanguages=listOf("eng"))
        val result=audioPlaybackAttempt(PlaybackRequest(live.id), true, { _, _ -> order+="options";live }, { requests+=it;order+="resolve";PlaybackDescriptor(it.id, PlaybackMode.PROXY, live=true) }, {order+="stop-old"})
        assertTrue(result is AudioPlaybackAttempt.Ready)
        assertEquals(listOf("options","stop-old","resolve"),order)
        assertEquals(PlaybackRequest(live.id,variantId=live.id,allowUnverifiedAudio=true),requests.single())
        assertEquals(requests.single(),(result as AudioPlaybackAttempt.Ready).request)
        // The changed server names the channel's variant itself; nothing else differs.
        val named=audioPlaybackAttempt(PlaybackRequest(live.id), true, { _, _ -> live.copy(automaticVariantId=live.id) }, { PlaybackDescriptor(it.id, PlaybackMode.PROXY, live=true) }) as AudioPlaybackAttempt.Ready
        assertEquals(PlaybackRequest(live.id,variantId=live.id,allowUnverifiedAudio=true),named.request)
    }

    @Test fun channelVariantAlreadyNamedCarriesConsentWithoutReselection() = runTest {
        val requests=mutableListOf<PlaybackRequest>()
        audioPlaybackAttempt(PlaybackRequest("ch:s:7",variantId="ch:s:7"),false,{_,_->error("no reselection")},{requests+=it;PlaybackDescriptor(it.id,PlaybackMode.PROXY,live=true)})
        assertEquals(listOf(PlaybackRequest("ch:s:7",variantId="ch:s:7",allowUnverifiedAudio=true)),requests)
        assertTrue(channelPlayback("ch:s:7") && channelPlayback("prog:4") && !channelPlayback("lib:s:1") && !channelPlayback("rec:9"))
    }

    @Test fun guideAndArchivePlayWithExactRequestParametersAndNoChoice() = runTest {
        for(request in listOf(PlaybackRequest("prog:4",catchupProgrammeId="prog:4"),PlaybackRequest("ch:s:7",rewindAt="2026-11-01T18:30:00Z"))) {
            val live=choice.copy(id=request.id,options=choice.options.map { it.copy(variantId="ch:s:7") },preferredLanguages=listOf("eng"))
            val ready=audioPlaybackAttempt(request,true,{_,_->live},{PlaybackDescriptor("ch:s:7",PlaybackMode.PROXY)}) as AudioPlaybackAttempt.Ready
            assertEquals(request.copy(variantId="ch:s:7",allowUnverifiedAudio=true),ready.request)
        }
    }

    @Test fun filmsStillAskWhenNoVerifiedPreferredAudio() = runTest {
        val film=choice.copy(preferredLanguages=listOf("eng"))
        assertTrue(audioPlaybackAttempt(PlaybackRequest(film.id),true,{_,_->film},{error("must choose")}) is AudioPlaybackAttempt.Choice)
        val unverified=film.copy(automaticVariantId="lib:s:1")
        assertTrue(audioPlaybackAttempt(PlaybackRequest(film.id),true,{_,_->unverified},{error("must choose")}) is AudioPlaybackAttempt.Choice)
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
