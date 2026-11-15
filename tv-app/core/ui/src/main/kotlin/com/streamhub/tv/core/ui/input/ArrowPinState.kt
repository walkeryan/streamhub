package com.streamhub.tv.core.ui.input

import android.view.KeyEvent

/**
 * One press of the D-pad in an arrow pattern, and the digit it stands for: where that arrow sits on
 * a phone keypad around 5. The server keeps an arrow pattern as these digits, so it is an ordinary
 * PIN to every check (and can still be typed as digits wherever a PIN is asked for).
 */
enum class Arrow(val digit: Char, val glyph: String) {
    Up('2', "↑"),
    Left('4', "←"),
    Right('6', "→"),
    Down('8', "↓"),
    ;

    companion object {
        /** The arrow a D-pad key enters; null for every other key, OK included. */
        fun ofKey(keyCode: Int): Arrow? = when (keyCode) {
            KeyEvent.KEYCODE_DPAD_UP -> Up
            KeyEvent.KEYCODE_DPAD_LEFT -> Left
            KeyEvent.KEYCODE_DPAD_RIGHT -> Right
            KeyEvent.KEYCODE_DPAD_DOWN -> Down
            else -> null
        }
    }
}

/**
 * The arrows pressed so far towards a pattern of [length] arrows, as a value so the rules are
 * unit-tested. There is no OK step: the pattern is sent the moment its last arrow is pressed, so
 * the length comes from the server (`pin_arrows`), and presses after that are ignored until the
 * answer comes back and the dialog starts a fresh state. The arrows themselves are never printed.
 */
class ArrowPinState private constructor(val length: Int, private val arrows: List<Arrow>) {
    constructor(length: Int) : this(length.coerceIn(MIN_ARROWS, MAX_ARROWS), emptyList())

    /** How many dots are filled. */
    val entered: Int get() = arrows.size
    val isComplete: Boolean get() = arrows.size >= length

    /** With one more arrow; unchanged once the pattern is complete. */
    fun add(arrow: Arrow): ArrowPinState = if (isComplete) this else ArrowPinState(length, arrows + arrow)
    fun clear(): ArrowPinState = if (arrows.isEmpty()) this else ArrowPinState(length, emptyList())

    /** The PIN to send, as digits, once the last arrow is in; null before that. */
    fun submit(): String? = if (isComplete) digitsOf(arrows) else null

    override fun toString(): String = "ArrowPinState($entered of $length)"

    companion object {
        const val MIN_ARROWS = 4
        const val MAX_ARROWS = 8

        fun digitsOf(arrows: List<Arrow>): String = arrows.joinToString("") { it.digit.toString() }
    }
}
