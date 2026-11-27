package com.streamhub.tv.shell

/** Two distinct presses at the root of the TV shell. Navigation never arms an exit. */
internal class ExitConfirmation(private val windowMs: Long = 3_000) {
    private var firstAt: Long? = null

    fun press(nowMs: Long): Boolean {
        val previous = firstAt
        if (previous != null && nowMs - previous in 0 until windowMs) {
            reset()
            return true
        }
        firstAt = nowMs
        return false
    }

    fun reset() { firstAt = null }
}
