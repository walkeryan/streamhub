package com.streamhub.tv.feature.home

import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.Row
import com.streamhub.tv.core.data.model.Tile
import org.junit.Assert.assertEquals
import org.junit.Test

class ArrangeRowsTest {
    private fun row(id: String) = Row(id, id, tiles = listOf(Tile("t-$id", MediaKind.MOVIE, "x")))

    @Test fun serverOrderWhenNoPrefs() =
        assertEquals(listOf("a", "b", "c"), HomeViewModel.arrange(listOf(row("a"), row("b"), row("c")), emptyList(), emptySet()).map { it.id })

    @Test fun appliesOrderAndHidden() =
        assertEquals(listOf("c", "a"), HomeViewModel.arrange(listOf(row("a"), row("b"), row("c")), listOf("c", "a", "b"), setOf("b")).map { it.id })

    @Test fun unknownRowsGoLast() =
        assertEquals(listOf("b", "a", "new"), HomeViewModel.arrange(listOf(row("a"), row("new"), row("b")), listOf("b", "a"), emptySet()).map { it.id })

    @Test fun emptyRowsDropped() =
        assertEquals(listOf("a"), HomeViewModel.arrange(listOf(row("a"), Row("e", "e")), emptyList(), emptySet()).map { it.id })
}
