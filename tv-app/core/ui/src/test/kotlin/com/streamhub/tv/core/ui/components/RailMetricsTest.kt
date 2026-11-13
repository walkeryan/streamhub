package com.streamhub.tv.core.ui.components

import com.streamhub.tv.core.ui.theme.Tokens
import org.junit.Assert.assertEquals
import org.junit.Test

/** design/tokens.json `rail.tv_*` must describe the rail the shell actually lays out. */
class RailMetricsTest {
    @Test fun collapsedRailIsSafeMarginPlusItemPlusGutter() {
        assertEquals(Tokens.Canvas.SafeMarginX + RailMetrics.ItemW + RailMetrics.InnerGutter, Tokens.Rail.TvCollapsedW)
        assertEquals(Tokens.Rail.TvCollapsedW, RailMetrics.CollapsedW)
    }

    @Test fun expandedRailKeepsTheWebRailsLabelRoom() {
        assertEquals(Tokens.Rail.TvCollapsedW + (Tokens.Rail.ExpandedW - Tokens.Rail.CollapsedW), Tokens.Rail.TvExpandedW)
        assertEquals(Tokens.Rail.TvExpandedW, RailMetrics.ExpandedW)
    }

    // ---- order and gating ----------------------------------------------------------------------

    private fun slots(access: RailAccess = RailAccess()) = RailOrder.slots(access).map { it.first }
    private val everything = RailAccess(joinRoom = true, recordings = true, requests = true)

    @Test fun theRailStartsWithThePrimaryDestinationsAndEndsWithSettings() {
        for (access in listOf(RailAccess(), everything)) {
            val order = slots(access)
            assertEquals(listOf(RailSlot.Home, RailSlot.Search, RailSlot.LiveTv, RailSlot.Movies, RailSlot.Shows, RailSlot.MyList), order.take(6))
            assertEquals(RailSlot.Settings, order.last())
            assertEquals(order.size, order.distinct().size)
        }
    }

    @Test fun secondaryDestinationsSitBetweenMyListAndSettings() {
        assertEquals(
            listOf(RailSlot.Sports, RailSlot.Discover, RailSlot.JoinRoom, RailSlot.Recordings, RailSlot.Requests, RailSlot.Sources),
            RailOrder.slots(everything).filter { it.second == RailGroup.Secondary }.map { it.first },
        )
        // Groups never interleave: primary, then secondary, then Settings.
        val groups = RailOrder.slots(everything).map { it.second }
        assertEquals(groups.sortedBy { it.ordinal }, groups)
        assertEquals(listOf(RailSlot.Settings), RailOrder.slots(everything).filter { it.second == RailGroup.System }.map { it.first })
    }

    @Test fun aDestinationTheUserMayNotUseIsAbsent() {
        val none = slots()
        for (gated in listOf(RailSlot.JoinRoom, RailSlot.Recordings, RailSlot.Requests)) assertEquals(false, gated in none)
        assertEquals(true, RailSlot.Requests in slots(RailAccess(requests = true)))
        assertEquals(false, RailSlot.Recordings in slots(RailAccess(requests = true)))
        assertEquals(true, RailSlot.Recordings in slots(RailAccess(recordings = true)))
        assertEquals(true, RailSlot.JoinRoom in slots(RailAccess(joinRoom = true)))
        // Each one alone leaves the others' places as they were.
        assertEquals(slots(), slots(RailAccess(requests = true)) - RailSlot.Requests)
        assertEquals(slots(), slots(RailAccess(recordings = true)) - RailSlot.Recordings)
        assertEquals(slots().size + 3, slots(everything).size)
    }
}
