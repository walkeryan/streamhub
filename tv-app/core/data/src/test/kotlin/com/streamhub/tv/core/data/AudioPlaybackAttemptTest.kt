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

    @Test fun theOnlySourcePlaysWithoutAChoiceEvenUnverified() = runTest {
        // The owner's photo: one IPTV source, "Audio unverified · Listed as English", and a chooser in front of it.
        val film = choice.copy(preferredLanguages = listOf("eng"))
        val requests = mutableListOf<PlaybackRequest>()
        val result = audioPlaybackAttempt(PlaybackRequest(film.id), true, { _, _ -> film }, { requests += it; PlaybackDescriptor(it.id, PlaybackMode.PROXY) })
        assertTrue(result is AudioPlaybackAttempt.Ready)
        assertEquals(PlaybackRequest(film.id, variantId = "lib:s:1", allowUnverifiedAudio = true), requests.single())
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
        // A variant named without consent (the web app's automatic pick) is played as named, consent added.
        val named = PlaybackRequest(choice.id, variantId = "lib:s:1")
        val ready = audioPlaybackAttempt(named, false, { _, _ -> error("must not reselect") }, { PlaybackDescriptor(it.id, PlaybackMode.PROXY) }) as AudioPlaybackAttempt.Ready
        assertEquals(named.copy(allowUnverifiedAudio = true), ready.request)
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

    @Test fun filmsNeverAskAndPreferredAudioStillSteers() = runTest {
        val spanish = PlaybackOption("lib:s:1", "s", "IPTV", "ES - Star Wars")
        val english = PlaybackOption("lib:s:2", "s", "IPTV", "EN - Star Wars", preferredAudioAvailable = true)
        val film = PlaybackOptions("tmdb:movie:11", listOf(spanish, english), preferredLanguages = listOf("eng"))
        suspend fun played(options: PlaybackOptions) =
            (audioPlaybackAttempt(PlaybackRequest(options.id), true, { _, _ -> options }, { PlaybackDescriptor(it.id, PlaybackMode.PROXY) }) as AudioPlaybackAttempt.Ready).request
        // The server's automatic pick wins; without one, a source with preferred audio; failing that, the first.
        assertEquals("lib:s:1", played(film.copy(automaticVariantId = "lib:s:1")).variantId)
        assertEquals("lib:s:2", played(film).variantId)
        assertEquals("lib:s:1", played(film.copy(options = listOf(spanish, english.copy(preferredAudioAvailable = false)))).variantId)
        assertTrue(played(film).allowUnverifiedAudio)
    }

    @Test fun verifiedAutomaticVariantResolvesWithoutConsentOrChooser() = runTest {
        val verified = choice.copy(options = choice.options.map { it.copy(audioVerified = true, preferredAudioAvailable = true) }, automaticVariantId = "lib:s:1", preferredLanguages = listOf("eng"))
        val requests = mutableListOf<PlaybackRequest>()
        val result = audioPlaybackAttempt(PlaybackRequest(choice.id), true, { _, _ -> verified }, { requests += it; PlaybackDescriptor(it.id, PlaybackMode.PROXY) })
        assertTrue(result is AudioPlaybackAttempt.Ready)
        assertEquals(1, requests.size)
        assertEquals("lib:s:1", requests.single().variantId)
        assertTrue(requests.single().allowUnverifiedAudio)
    }

}
