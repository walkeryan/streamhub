package com.streamhub.tv.feature.home

import com.streamhub.tv.core.data.model.BrowsePage
import com.streamhub.tv.core.data.model.CatalogFacet
import com.streamhub.tv.core.data.model.MediaKind
import com.streamhub.tv.core.data.model.Tile
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BrowseShelvesTest {
    private fun tile(id: String) = Tile(id, MediaKind.MOVIE, "Title $id")
    private fun tiles(n: Int, prefix: String = "t") = (1..n).map { tile("$prefix$it") }

    @Test fun shelvesKeepServerOrderAndDropRepeatsAndBlanks() {
        val shelves = shelvesOf(listOf(CatalogFacet("New Releases", 120), CatalogFacet("Action", 48), CatalogFacet(" ", 3), CatalogFacet("Action", 2)))
        assertEquals(listOf("New Releases", "Action"), shelves.map { it.name })
        assertEquals(listOf(120, 48), shelves.map { it.count })
        assertTrue(shelves.none { it.loaded || it.loading })
    }

    @Test fun firstPageFillsTheShelfUpToItsLimit() {
        val shelf = Shelf("Action", 48, loading = true).withFirstPage(BrowsePage(tiles(40), page = 1, total = 48, hasMore = true, pageSize = 40))
        assertEquals(SHELF_TILES, shelf.tiles.size)
        assertEquals("t1", shelf.tiles.first().id)
        assertTrue(shelf.loaded)
        assertFalse(shelf.loading)
        assertTrue(shelf.hasMore)
    }

    @Test fun smallCategoryNeedsNoSeeAll() {
        val shelf = Shelf("Shorts", 3).withFirstPage(BrowsePage(tiles(3), page = 1, total = 3, hasMore = false, pageSize = 40))
        assertEquals(3, shelf.tiles.size)
        assertFalse(shelf.hasMore)
    }

    @Test fun seeAllAppearsWhenTheRowIsCutOrTheCountSaysThereIsMore() {
        // More tiles than the row shows, even though the server has no further page.
        assertTrue(Shelf("Drama", 30).withFirstPage(BrowsePage(tiles(30), total = 30, hasMore = false)).hasMore)
        // A filtered first page is short, but the category itself is larger than what came back.
        assertTrue(Shelf("Drama", 30).withFirstPage(BrowsePage(tiles(5), total = 5, hasMore = false)).hasMore)
    }

    @Test fun repeatedTitlesInAPageAreShownOnce() {
        val shelf = Shelf("Mixed", 2).withFirstPage(BrowsePage(listOf(tile("a"), tile("b"), tile("a")), total = 2, hasMore = false))
        assertEquals(listOf("a", "b"), shelf.tiles.map { it.id })
    }

    @Test fun shelvesThatLoadedEmptyAreHidden() {
        val shelves = listOf(Shelf("Waiting", 5), Shelf("Empty", 5, loaded = true), Shelf("Full", 5, tiles = tiles(2), loaded = true), Shelf("Failed", 5, failed = true))
        assertEquals(listOf("Waiting", "Full", "Failed"), shelves.visible().map { it.name })
    }

    @Test fun appendingAPageKeepsExistingTilesInPlace() {
        val first = tiles(3)
        val merged = appendTiles(first, listOf(tile("t3"), tile("t4"), tile("t5")))
        assertEquals(listOf("t1", "t2", "t3", "t4", "t5"), merged.map { it.id })
        assertEquals(first, merged.take(3))
        assertEquals(first, appendTiles(first, emptyList()))
    }

    @Test fun openedKeysTellShelvesAndTilesApart() {
        assertEquals(openedKey("Action", "t1"), openedKey("Action", "t1"))
        assertNotEquals(openedKey("Action", "t1"), openedKey("Drama", "t1"))
        assertNotEquals(openedKey("Action", "t1"), openedKey("Action", SEE_ALL_ID))
        assertNotEquals(openedKey(null, "t1"), openedKey("Action", "t1"))
    }
}
