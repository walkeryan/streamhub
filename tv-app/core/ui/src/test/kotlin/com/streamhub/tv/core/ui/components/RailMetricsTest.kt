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
}
