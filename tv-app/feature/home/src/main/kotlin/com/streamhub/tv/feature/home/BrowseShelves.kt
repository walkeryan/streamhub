package com.streamhub.tv.feature.home

import com.streamhub.tv.core.data.model.BrowsePage
import com.streamhub.tv.core.data.model.CatalogFacet
import com.streamhub.tv.core.data.model.Tile

/** One category row on Movies/Shows. Its tiles arrive when the row first scrolls into view. */
data class Shelf(
    val name: String,
    val count: Int,
    val tiles: List<Tile> = emptyList(),
    val loading: Boolean = false,
    val loaded: Boolean = false,
    val failed: Boolean = false,
    /** The category holds more than the row shows: the row ends in a "See all" tile. */
    val hasMore: Boolean = false,
)

/** A shelf shows the start of its category; the rest is one "See all" away. */
internal const val SHELF_TILES = 20

/** Shelves in server order. A repeated name would collide as a list key, so the first one wins. */
internal fun shelvesOf(categories: List<CatalogFacet>): List<Shelf> =
    categories.filter { it.name.isNotBlank() }.distinctBy { it.name }.map { Shelf(it.name, it.count) }

/** The first page of a category applied to its shelf. */
internal fun Shelf.withFirstPage(page: BrowsePage): Shelf {
    val all = page.tiles.distinctBy { it.id }
    return copy(
        tiles = all.take(SHELF_TILES),
        loading = false, loaded = true, failed = false,
        hasMore = page.more || all.size > SHELF_TILES || count > all.size,
    )
}

/** Shelves worth a row: one that loaded empty (a genre filter can empty a category) is dropped. */
internal fun List<Shelf>.visible(): List<Shelf> = filterNot { it.loaded && it.tiles.isEmpty() }

/**
 * Next grid page appended to what is already on screen. Existing tiles keep their place, and a
 * title the server repeats across pages is not added twice (tile ids are grid keys).
 */
internal fun appendTiles(existing: List<Tile>, next: List<Tile>): List<Tile> = (existing + next).distinctBy { it.id }

/** Saveable marker for the tile that opened another screen, so Back can return focus to it. */
internal fun openedKey(shelf: String?, tileId: String): String = "${shelf.orEmpty()}\u0000$tileId"

/** [openedKey] id of a shelf's "See all" tile. */
internal const val SEE_ALL_ID = "\u0001see-all"
