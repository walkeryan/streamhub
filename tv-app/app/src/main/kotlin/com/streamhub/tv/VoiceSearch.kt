package com.streamhub.tv

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.view.KeyEvent

/** A single recognition session, owned by the activity and displayed by the search field. */
class VoiceSearch(private val context: Context, private val report: (String, String?, String?) -> Unit) {
    private var active: SpeechRecognizer? = null
    val listening get() = active != null

    fun start() {
        if (listening) return
        try {
            val recognizer = SpeechRecognizer.createSpeechRecognizer(context)
            active = recognizer
            recognizer.setRecognitionListener(object : RecognitionListener {
                private fun text(bundle: Bundle?) = bundle?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull { it.isNotBlank() }?.trim()

                override fun onPartialResults(partialResults: Bundle?) {
                    if (active === recognizer) text(partialResults)?.let { report("partial", it, null) }
                }

                override fun onResults(results: Bundle?) {
                    if (active !== recognizer) return
                    val words = text(results)
                    finish(if (words == null) "error" else "final", words, if (words == null) "no_match" else null)
                }

                override fun onError(error: Int) {
                    if (active !== recognizer) return
                    val reason = when (error) {
                        SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "no_match"
                        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "permission"
                        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "busy"
                        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "network"
                        SpeechRecognizer.ERROR_AUDIO -> "unavailable"
                        else -> "other"
                    }
                    finish("error", null, reason)
                }

                override fun onReadyForSpeech(params: Bundle?) {}
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() {}
                override fun onEvent(eventType: Int, params: Bundle?) {}
            })
            report("listening", null, null)
            recognizer.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            })
        } catch (_: SecurityException) {
            finish("error", null, "permission")
        } catch (_: RuntimeException) {
            finish("error", null, "unavailable")
        }
    }

    fun stop() {
        if (listening) finish("idle", null, null)
    }

    private fun finish(state: String, words: String?, error: String?) {
        val previous = active
        active = null
        previous?.destroy()
        report(state, words, error)
    }

    companion object {
        fun available(context: Context) = SpeechRecognizer.isRecognitionAvailable(context)
        fun isSearchKey(keyCode: Int) = keyCode == KeyEvent.KEYCODE_SEARCH || keyCode == KeyEvent.KEYCODE_VOICE_ASSIST
    }
}
