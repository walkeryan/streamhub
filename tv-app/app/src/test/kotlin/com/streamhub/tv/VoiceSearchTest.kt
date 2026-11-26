package com.streamhub.tv

import android.app.Application
import android.os.Bundle
import android.os.Looper
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowSpeechRecognizer

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class VoiceSearchTest {
    private val events = mutableListOf<Triple<String, String?, String?>>()
    private val voice = VoiceSearch(RuntimeEnvironment.getApplication()) { state, words, error ->
        events.add(Triple(state, words, error))
    }

    private fun words(text: String) = Bundle().apply {
        putStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION, arrayListOf(text))
    }

    private fun start(): ShadowSpeechRecognizer {
        voice.start()
        shadowOf(Looper.getMainLooper()).idle()
        return shadowOf(ShadowSpeechRecognizer.getLatestSpeechRecognizer())
    }

    @Test fun partialAndFinalResultsComeFromTheSameInAppSession() {
        val session = start()
        assertTrue(session.lastRecognizerIntent.getBooleanExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false))
        voice.start()
        session.triggerOnPartialResults(words("mar"))
        session.triggerOnResults(words(" Mars "))
        assertEquals(listOf("listening", "partial", "final"), events.map { it.first })
        assertEquals("Mars", events.last().second)
        assertTrue(session.isDestroyed)
        assertFalse(voice.listening)
    }

    @Test fun cancelIgnoresLateResultsAndAFreshSessionCanReportAnError() {
        val old = start()
        voice.stop()
        old.triggerOnResults(words("discarded"))
        val next = start()
        next.triggerOnError(SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS)
        assertEquals(listOf("listening", "idle", "listening", "error"), events.map { it.first })
        assertEquals("permission", events.last().third)
        assertTrue(next.isDestroyed)
    }
}
