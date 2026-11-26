package com.streamhub.tv.shell

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ExitConfirmationTest {
    @Test fun twoSeparatePressesExitOnlyWithinTheWindow() {
        val exit = ExitConfirmation()
        assertFalse(exit.press(0))
        assertFalse(exit.press(3_000))
        assertTrue(exit.press(5_999))
        assertFalse(exit.press(6_000))
        exit.reset()
        assertFalse(exit.press(6_001))
    }
}
