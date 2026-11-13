package com.streamhub.tv.core.ui.input

import android.view.KeyEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class PinPadStateTest {
    private fun typed(pin: String) = pin.fold(PinPadState()) { s, c -> s.append(c - '0') }

    @Test fun startsEmptyAndCannotBeSubmitted() {
        val s = PinPadState()
        assertEquals("", s.digits)
        assertFalse(s.canSubmit)
        assertNull(s.submit())
    }

    @Test fun fourDigitsIsTheShortestPin() {
        assertNull(typed("123").submit())
        assertEquals("1234", typed("1234").submit())
        assertTrue(typed("1234").canSubmit)
    }

    @Test fun eightDigitsIsTheLongestAndFurtherDigitsAreIgnored() {
        val full = typed("12345678")
        assertTrue(full.isFull)
        assertEquals("12345678", full.submit())
        assertSame(full, full.append(9))
        assertEquals("12345678", typed("1234567890").digits)
    }

    @Test fun onlyDigitsAreAccepted() {
        val s = typed("12")
        assertSame(s, s.append(10))
        assertSame(s, s.append(-1))
        assertEquals("120", s.append(0).digits)
    }

    @Test fun backspaceAndClear() {
        assertEquals("123", typed("1234").backspace().digits)
        assertFalse(typed("1234").backspace().canSubmit)
        assertEquals(PinPadState(), typed("1234").clear())
        val empty = PinPadState()
        assertSame(empty, empty.backspace())
        assertSame(empty, empty.clear())
    }

    @Test fun digitsAreMaskedAndNeverPrinted() {
        val s = typed("90210")
        assertEquals("•••••", s.masked)
        assertFalse(s.toString().contains("90210"))
        assertEquals("", PinPadState().masked)
    }

    @Test fun remoteDigitKeysTypeAndDeleteRemoves() {
        var s = PinPadState()
        for (key in listOf(KeyEvent.KEYCODE_4, KeyEvent.KEYCODE_NUMPAD_2, KeyEvent.KEYCODE_0, KeyEvent.KEYCODE_9)) s = s.onKey(key)!!
        assertEquals("4209", s.digits)
        assertEquals("420", s.onKey(KeyEvent.KEYCODE_DEL)!!.digits)
        assertEquals("420", s.onKey(KeyEvent.KEYCODE_FORWARD_DEL)!!.digits)
    }

    @Test fun otherKeysAreLeftToTheFocusedButton() {
        val s = typed("12")
        for (key in listOf(KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_BACK, KeyEvent.KEYCODE_A)) {
            assertNull(s.onKey(key))
        }
    }
}
