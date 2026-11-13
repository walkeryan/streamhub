package com.streamhub.tv.core.ui.input

import android.view.KeyEvent

/**
 * The digits typed into the PIN pad, as a value so the rules are unit-tested: [MIN_DIGITS] to
 * [MAX_DIGITS] digits, nothing else, and a PIN can be submitted only once it is long enough.
 * The digits themselves are never shown ([masked]) or printed ([toString]).
 */
data class PinPadState(val digits: String = "") {
    val length: Int get() = digits.length
    val isFull: Boolean get() = digits.length >= MAX_DIGITS
    val canSubmit: Boolean get() = digits.length in MIN_DIGITS..MAX_DIGITS

    /** One dot per digit typed. */
    val masked: String get() = "•".repeat(digits.length)

    /** With one more digit; unchanged when full or when [digit] is not 0-9. */
    fun append(digit: Int): PinPadState = if (digit !in 0..9 || isFull) this else PinPadState(digits + digit)
    fun backspace(): PinPadState = if (digits.isEmpty()) this else PinPadState(digits.dropLast(1))
    fun clear(): PinPadState = if (digits.isEmpty()) this else PinPadState()

    /** The PIN to send, or null while it is too short. */
    fun submit(): String? = digits.takeIf { canSubmit }

    /**
     * A remote or keyboard key: 0-9 (number row or keypad) types a digit, Delete/Backspace removes
     * one. null when the key is not the pad's, so D-pad and Select reach the focused button.
     */
    fun onKey(keyCode: Int): PinPadState? = when {
        keyCode == KeyEvent.KEYCODE_DEL || keyCode == KeyEvent.KEYCODE_FORWARD_DEL -> backspace()
        else -> ChannelNumberEntry.digitOf(keyCode)?.let(::append)
    }

    override fun toString(): String = "PinPadState(${digits.length} digits)"

    companion object {
        const val MIN_DIGITS = 4
        const val MAX_DIGITS = 8
    }
}
