package com.streamhub.tv

import android.content.Context
import android.content.Intent
import android.speech.RecognizerIntent

/**
 * Voice search through the TV's own speech recogniser (on Google TV it listens through the remote's
 * microphone). It is asked with the RECOGNIZE_SPEECH intent, which needs no microphone permission of
 * StreamHub's: the recogniser app holds it. The remote's Assistant button belongs to Google and may
 * never reach this app; the page's microphone button and, where the system passes them on, the remote's
 * search keys do.
 */
object VoiceSearch {
    fun intent(language: String? = null): Intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_WEB_SEARCH)
        putExtra(RecognizerIntent.EXTRA_PROMPT, "Search StreamHub")
        putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        language?.let { putExtra(RecognizerIntent.EXTRA_LANGUAGE, it) }
    }

    /** Whether anything on this TV answers the intent. */
    fun available(context: Context): Boolean =
        context.packageManager.queryIntentActivities(intent(), 0).isNotEmpty()

    /** What was said, from the recogniser's result; null when nothing was. */
    fun heard(data: Intent?): String? =
        data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()?.trim()?.takeIf { it.isNotEmpty() }

    /** The remote keys that mean "search" when the system hands them to the app. */
    fun isSearchKey(keyCode: Int): Boolean = keyCode == android.view.KeyEvent.KEYCODE_SEARCH || keyCode == android.view.KeyEvent.KEYCODE_VOICE_ASSIST
}
