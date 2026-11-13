package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.api.StreamRefusals
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.PlaybackRequest
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** 403 `playback_paused` on a stream: one silent re-resolve with a fresh generation, then the dialog. */
class PausedRetryTest {
    private val paused = ApiException(403, ApiError("playback_paused", "Playback was stopped by an administrator. Wait for playback to be resumed."), "x")

    /** The player's reaction, as PlayerViewModel wires it: what was asked of the server and what the viewer saw. */
    private class Playback(val serverStopped: Boolean = false, val resolveFails: Exception? = null) {
        val retry = PausedRetry()
        val calls = mutableListOf<String>()
        var dialog: String? = null
        suspend fun streamAnsweredPaused(refusal: ApiException) {
            if (!retry.claim()) { dialog = ApiErrors.describe(refusal).title; return }
            resumeWithFreshGeneration(
                generation = { calls += "generation"; if (serverStopped) throw refusal; "epoch-2" },
                resolve = { generation -> calls += "resolve:$generation"; resolveFails?.let { throw it } },
            ).onFailure { dialog = ApiErrors.describe(it).title }
        }
    }

    @Test fun theFirstAnswerIsResolvedAgainOnceWithAFreshGenerationAndNothingIsShown() = runBlocking<Unit> {
        val playback = Playback()
        playback.streamAnsweredPaused(paused)
        assertEquals(listOf("generation", "resolve:epoch-2"), playback.calls)
        assertNull("a server restart is not put to the viewer", playback.dialog)
    }

    @Test fun aSecondAnswerForTheSamePlaybackIsShownAndNotRetried() = runBlocking<Unit> {
        val playback = Playback()
        playback.streamAnsweredPaused(paused)
        playback.streamAnsweredPaused(paused)
        assertEquals("only the one silent retry", listOf("generation", "resolve:epoch-2"), playback.calls)
        assertEquals("Playback is stopped", playback.dialog)
        playback.streamAnsweredPaused(paused)
        assertEquals(2, playback.calls.size)
    }

    @Test fun aServerThatReallyIsStoppedRefusesTheGenerationAndThatIsShown() = runBlocking<Unit> {
        val playback = Playback(serverStopped = true)
        playback.streamAnsweredPaused(paused)
        assertEquals("nothing is resolved against a stopped server", listOf("generation"), playback.calls)
        assertEquals("Playback is stopped", playback.dialog)
    }

    @Test fun aFreshResolveThatFailsIsShownInItsOwnWords() = runBlocking<Unit> {
        val playback = Playback(resolveFails = ApiException(409, ApiError("source_busy", "busy"), "busy"))
        playback.streamAnsweredPaused(paused)
        assertEquals(listOf("generation", "resolve:epoch-2"), playback.calls)
        assertEquals("No free stream", playback.dialog)
    }

    @Test fun onceThePictureMovesAgainTheNextStopIsAFirstOne() = runBlocking<Unit> {
        val playback = Playback()
        playback.streamAnsweredPaused(paused)
        playback.retry.reset()
        playback.streamAnsweredPaused(paused)
        assertEquals(listOf("generation", "resolve:epoch-2", "generation", "resolve:epoch-2"), playback.calls)
        assertNull(playback.dialog)
    }

    @Test fun claimIsTrueExactlyOncePerPlayback() {
        val retry = PausedRetry()
        assertTrue(retry.claim())
        assertFalse(retry.claim())
        assertFalse(retry.claim())
        retry.reset()
        assertTrue(retry.claim())
    }

    @Test fun cancellationIsNotAnAnswer() = runBlocking<Unit> {
        try {
            resumeWithFreshGeneration<Unit>({ throw CancellationException("left the player") }) { fail("not resolved") }
            fail("cancellation must propagate")
        } catch (_: CancellationException) { }
    }

    @Test fun theStreamsRefusalIsReadAndWordedAndTheReResolveCarriesTheGeneration() {
        val body = """{"code":"playback_paused","message":"Playback was stopped by an administrator. Wait for playback to be resumed."}""".toByteArray()
        val refusal = StreamRefusals.parse(403, body)
        assertNotNull(refusal)
        assertEquals(StreamRefusals.PLAYBACK_PAUSED, refusal!!.error?.code)
        assertFalse("not settled by a token refresh", StreamRefusals.PLAYBACK_PAUSED in StreamRefusals.ABOUT_THE_SESSION)
        val shown = ApiErrors.describe(refusal)
        assertEquals("Playback is stopped", shown.title)
        assertTrue(shown.canRetry)
        val request = PlaybackRequest("lib:s:1", variantId = "v1")
        assertEquals("""{"id":"lib:s:1","client":"android_tv","variant_id":"v1","allow_unverified_audio":false,"recovery_generation":"epoch-2"}""",
            StreamHubJson.encodeToString(PlaybackRequest.serializer(), request.copy(recoveryGeneration = "epoch-2")))
    }

    @Test fun aPlaybackRequestNeverCarriesAQualityCap() {
        // max_height is the web player's; it is ignored for android_tv and is not part of what this app sends.
        val sent = StreamHubJson.encodeToString(PlaybackRequest.serializer(), PlaybackRequest("lib:s:1", sourceId = "s", catchupProgrammeId = "p", variantId = "v", allowUnverifiedAudio = true, rewindAt = "2026-11-13T20:00:00Z"))
        assertFalse(sent.contains("max_height"))
        assertTrue(sent.contains("\"client\":\"android_tv\""))
        assertFalse("no generation unless it is the re-resolve after a stop", sent.contains("recovery_generation"))
    }
}
