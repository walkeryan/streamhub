package com.streamhub.tv.core.ui.input

import android.view.KeyEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ChannelNumberEntryTest {
    private data class Ch(val number: Int, val name: String)
    private val channels = listOf(Ch(1, "One"), Ch(7, "Seven"), Ch(105, "Sports"), Ch(105, "Sports duplicate"))

    @Test fun digitsAccumulateInOrder() {
        var entry = ""
        for (d in listOf(1, 0, 5)) entry = ChannelNumberEntry.append(entry, d)
        assertEquals("105", entry)
        assertEquals(105, ChannelNumberEntry.number(entry))
    }

    @Test fun oldestDigitDropsOffPastTheLimit() {
        var entry = ""
        for (d in 1..7) entry = ChannelNumberEntry.append(entry, d)
        assertEquals("34567", entry)
        assertEquals(ChannelNumberEntry.MAX_DIGITS, entry.length)
    }

    @Test fun nothingTypedIsNothingToCommit() {
        assertNull(ChannelNumberEntry.number(""))
        assertNull(ChannelNumberEntry.match("", channels) { it.number })
    }

    @Test fun matchFindsTheFirstChannelWithThatNumber() {
        assertEquals("Sports", ChannelNumberEntry.match("105", channels) { it.number }?.name)
        assertEquals("Seven", ChannelNumberEntry.match("7", channels) { it.number }?.name)
        assertNull(ChannelNumberEntry.match("8", channels) { it.number })
    }

    @Test fun leadingZerosAreIgnored() {
        assertEquals(7, ChannelNumberEntry.number("007"))
        assertEquals("Seven", ChannelNumberEntry.match("007", channels) { it.number }?.name)
        assertEquals(0, ChannelNumberEntry.number("0"))
    }

    @Test fun bothKeyRowsProduceDigits() {
        assertEquals((0..9).toList(), (KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9).map { ChannelNumberEntry.digitOf(it) })
        assertEquals((0..9).toList(), (KeyEvent.KEYCODE_NUMPAD_0..KeyEvent.KEYCODE_NUMPAD_9).map { ChannelNumberEntry.digitOf(it) })
        assertNull(ChannelNumberEntry.digitOf(KeyEvent.KEYCODE_DPAD_CENTER))
        assertNull(ChannelNumberEntry.digitOf(KeyEvent.KEYCODE_STAR))
    }

    @Test fun commitWindowMatchesThePlayers() = assertEquals(2_000L, ChannelNumberEntry.COMMIT_MS)
}
