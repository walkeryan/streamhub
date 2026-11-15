package com.streamhub.tv.core.ui.input

import android.view.KeyEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class ArrowPinStateTest {
    private fun pressed(length: Int, vararg arrows: Arrow) = arrows.fold(ArrowPinState(length)) { s, a -> s.add(a) }

    @Test fun eachArrowIsTheDigitBesideFiveOnAPhoneKeypad() {
        assertEquals('2', Arrow.Up.digit)
        assertEquals('4', Arrow.Left.digit)
        assertEquals('6', Arrow.Right.digit)
        assertEquals('8', Arrow.Down.digit)
        assertEquals("24682468", ArrowPinState.digitsOf(listOf(Arrow.Up, Arrow.Left, Arrow.Right, Arrow.Down).let { it + it }))
    }

    @Test fun theDpadKeysAreTheArrowsAndNothingElseIs() {
        assertEquals(Arrow.Up, Arrow.ofKey(KeyEvent.KEYCODE_DPAD_UP))
        assertEquals(Arrow.Down, Arrow.ofKey(KeyEvent.KEYCODE_DPAD_DOWN))
        assertEquals(Arrow.Left, Arrow.ofKey(KeyEvent.KEYCODE_DPAD_LEFT))
        assertEquals(Arrow.Right, Arrow.ofKey(KeyEvent.KEYCODE_DPAD_RIGHT))
        // OK never enters an arrow (and never submits); neither do Back or the number keys.
        for (key in listOf(KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_BACK, KeyEvent.KEYCODE_2, KeyEvent.KEYCODE_8)) {
            assertNull(Arrow.ofKey(key))
        }
    }

    @Test fun nothingIsSentBeforeTheLastArrow() {
        val s = pressed(4, Arrow.Up, Arrow.Up, Arrow.Down)
        assertEquals(3, s.entered)
        assertFalse(s.isComplete)
        assertNull(s.submit())
    }

    @Test fun theLastArrowSendsThePatternAsDigits() {
        val s = pressed(6, Arrow.Up, Arrow.Up, Arrow.Down, Arrow.Down, Arrow.Left, Arrow.Right)
        assertTrue(s.isComplete)
        assertEquals("228846", s.submit())
    }

    @Test fun pressesAfterTheLastArrowAreIgnored() {
        val full = pressed(4, Arrow.Left, Arrow.Left, Arrow.Right, Arrow.Right)
        assertSame(full, full.add(Arrow.Up))
        assertEquals("4466", full.submit())
    }

    @Test fun clearingStartsThePatternAgain() {
        val s = pressed(4, Arrow.Up, Arrow.Down).clear()
        assertEquals(0, s.entered)
        assertEquals(4, s.length)
        assertSame(s, s.clear())
        assertEquals("8888", pressed(4, Arrow.Down, Arrow.Down, Arrow.Down, Arrow.Down).submit())
    }

    @Test fun aLengthOutsideFourToEightIsHeldToThoseBounds() {
        assertEquals(4, ArrowPinState(0).length)
        assertEquals(4, ArrowPinState(-3).length)
        assertEquals(8, ArrowPinState(12).length)
        assertEquals(6, ArrowPinState(6).length)
    }

    @Test fun thePatternIsNeverPrinted() {
        val s = pressed(5, Arrow.Down, Arrow.Down, Arrow.Right)
        assertEquals("ArrowPinState(3 of 5)", s.toString())
        assertFalse(s.toString().contains("886"))
    }
}
