package com.streamhub.tv.core.ui.input

import android.view.KeyEvent

/**
 * Typing a channel number on the remote. The rules are the same wherever channels are shown (the
 * player, the channel list, the guide): up to [MAX_DIGITS] digits, committed after [COMMIT_MS]
 * without another digit or at once with Select. Pure, so the rules are unit-tested.
 */
object ChannelNumberEntry {
    const val COMMIT_MS = 2_000L
    const val MAX_DIGITS = 5

    /** 0-9 from the number row or the numeric keypad of an Android key code; null for anything else. */
    fun digitOf(keyCode: Int): Int? = when (keyCode) {
        in KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9 -> keyCode - KeyEvent.KEYCODE_0
        in KeyEvent.KEYCODE_NUMPAD_0..KeyEvent.KEYCODE_NUMPAD_9 -> keyCode - KeyEvent.KEYCODE_NUMPAD_0
        else -> null
    }

    /** [entry] with one more digit; past [MAX_DIGITS] the oldest digit drops off, as on a set-top box. */
    fun append(entry: String, digit: Int): String = (entry + digit.coerceIn(0, 9)).takeLast(MAX_DIGITS)

    /** The number typed so far, or null when there is nothing to commit. */
    fun number(entry: String): Int? = entry.toIntOrNull()

    /** The channel a typed [entry] names among [channels], if any. Leading zeros are ignored ("007" is 7). */
    fun <T> match(entry: String, channels: List<T>, numberOf: (T) -> Int): T? =
        number(entry)?.let { n -> channels.firstOrNull { numberOf(it) == n } }
}
