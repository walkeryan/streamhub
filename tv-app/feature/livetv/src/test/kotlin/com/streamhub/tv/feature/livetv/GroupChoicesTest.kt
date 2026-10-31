package com.streamhub.tv.feature.livetv

import com.streamhub.tv.core.data.model.ChannelGroup
import org.junit.Assert.assertEquals
import org.junit.Test

class GroupChoicesTest {
    @Test fun duplicateNamesMatchServerSelectionAndCombineCounts() {
        val groups = listOf(
            ChannelGroup("a", "source-a", "Sports", 12, false),
            ChannelGroup("b", "source-b", "Sports", 8, false),
            ChannelGroup("c", "source-c", "Sports", 100, true),
        )
        assertEquals(listOf(GroupChoice("Sports", 20)), groupChoices(groups))
    }

    @Test fun searchFindsLastGroupInLargeCatalogWithoutTruncatingBeforeFiltering() {
        val groups = (0 until 804).map { ChannelGroup("$it", "source", "Group ${it.toString().padStart(3, '0')}", it + 1, false) }
        assertEquals(listOf(GroupChoice("Group 803", 804)), groupChoices(groups, "  GROUP 803  "))
        assertEquals(804, groupChoices(groups).size)
        assertEquals(emptyList<GroupChoice>(), groupChoices(groups, "missing"))
    }
}
