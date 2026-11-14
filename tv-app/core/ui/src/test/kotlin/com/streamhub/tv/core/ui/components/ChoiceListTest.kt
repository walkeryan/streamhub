package com.streamhub.tv.core.ui.components

import org.junit.Assert.assertEquals
import org.junit.Test

class ChoiceListTest {
    @Test fun focusStartsOnTheChoiceInForce() {
        assertEquals(1, initialChoiceIndex(listOf("a" to true, "b" to true, "c" to true), "b"))
    }

    @Test fun withoutAChoiceFocusStartsOnTheFirstRowThatCanTakeIt() {
        assertEquals(0, initialChoiceIndex(listOf("a" to true, "b" to true), null))
        assertEquals(1, initialChoiceIndex(listOf("a" to false, "b" to true), "gone"))
    }

    @Test fun aChoiceThatCannotBeFocusedIsPassedOver() {
        assertEquals(0, initialChoiceIndex(listOf("a" to true, "b" to false), "b"))
        assertEquals(-1, initialChoiceIndex(listOf("a" to false), "a"))
        assertEquals(-1, initialChoiceIndex(emptyList(), null))
    }
}
