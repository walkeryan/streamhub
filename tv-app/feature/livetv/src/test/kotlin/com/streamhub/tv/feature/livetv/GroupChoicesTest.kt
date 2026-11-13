package com.streamhub.tv.feature.livetv

import com.streamhub.tv.core.data.model.ChannelGroup
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class GroupChoicesTest {
    private fun top(id: String, name: String, count: Int, source: String = "s1", hidden: Boolean = false) =
        ChannelGroup(id, source, name, count, hidden, parent = null, path = name)
    private fun child(id: String, parent: String, name: String, count: Int, source: String = "s1", hidden: Boolean = false) =
        ChannelGroup(id, source, name, count, hidden, parent = parent, path = "$parent / $name")

    private val nested = listOf(
        top("1", "Sports", 30),
        child("2", "Sports", "Football", 12),
        child("3", "Sports", "Hockey", 8),
        top("4", "News", 9),
        top("5", "Kids", 4, hidden = true),
    )

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

    @Test fun theSearchableListNamesAChildByItsPath() {
        assertEquals(listOf("News", "Sports", "Sports / Football", "Sports / Hockey"), groupChoices(nested).map { it.path })
        assertEquals(listOf(GroupChoice("Sports / Hockey", 8, "Sports / Hockey")), groupChoices(nested, "hockey"))
    }

    @Test fun chipsAreTheTopLevelOnlyWithChildrenUnderTheirParent() {
        val tree = groupTree(nested)
        assertEquals(listOf("Sports", "News"), tree.map { it.name })
        assertEquals(listOf(GroupChild("Football", "Sports / Football", 12), GroupChild("Hockey", "Sports / Hockey", 8)), tree[0].children)
        // The parent's count is the server's: its own channels and its children's.
        assertEquals(30, tree[0].channelCount)
        assertEquals(emptyList<GroupChild>(), tree[1].children)
    }

    @Test fun aServerWithoutNestingListsEveryGroupAtTheTopLevel() {
        val flat = listOf(ChannelGroup("a", "s", "Sports", 3, false), ChannelGroup("b", "s", "News", 2, false))
        assertEquals(listOf(GroupNode("Sports", 3), GroupNode("News", 2)), groupTree(flat))
    }

    @Test fun theSameParentInTwoSourcesIsOneChipAndItsChildrenAreMerged() {
        val tree = groupTree(listOf(
            top("1", "Sports", 10, source = "a"), child("2", "Sports", "Football", 4, source = "a"),
            top("3", "Sports", 6, source = "b"), child("4", "Sports", "Football", 2, source = "b"), child("5", "Sports", "Golf", 1, source = "b"),
        ))
        assertEquals(1, tree.size)
        assertEquals(16, tree[0].channelCount)
        assertEquals(listOf(GroupChild("Football", "Sports / Football", 6), GroupChild("Golf", "Sports / Golf", 1)), tree[0].children)
    }

    @Test fun aChildWhoseParentRowIsMissingStillHasAChip() {
        val tree = groupTree(listOf(child("2", "Sports", "Football", 4), child("3", "Sports", "Golf", 1)))
        assertEquals(listOf(GroupNode("Sports", 5, listOf(GroupChild("Football", "Sports / Football", 4), GroupChild("Golf", "Sports / Golf", 1)))), tree)
    }

    @Test fun aHiddenChildIsLeftOut() {
        val tree = groupTree(listOf(top("1", "Sports", 5), child("2", "Sports", "Football", 4), child("3", "Sports", "Golf", 1, hidden = true)))
        assertEquals(listOf("Football"), tree[0].children.map { it.name })
    }

    @Test fun theChipNamesTheChildInForce() {
        val tree = groupTree(nested)
        assertEquals("Sports", groupChipLabel(tree[0], null))
        assertEquals("Sports", groupChipLabel(tree[0], "Sports"))
        assertEquals("Sports · Hockey", groupChipLabel(tree[0], "Sports / Hockey"))
        assertEquals("News", groupChipLabel(tree[1], "Sports / Hockey"))
    }

    @Test fun theChosenChipIsTheGroupOrTheParentOfTheChosenChild() {
        val tree = groupTree(nested)
        assertNull(tree.nodeFor(null))
        assertEquals("Sports", tree.nodeFor("Sports")?.name)
        assertEquals("Sports", tree.nodeFor("Sports / Football")?.name)
        assertEquals("News", tree.nodeFor("News")?.name)
        assertNull(tree.nodeFor("Gone"))
    }

    @Test fun theListOfAParentIsAllOfItThenEachChildWithItsCount() {
        assertEquals(
            listOf(GroupMenuEntry("Sports", "All Sports", 30), GroupMenuEntry("Sports / Football", "Football", 12), GroupMenuEntry("Sports / Hockey", "Hockey", 8)),
            groupMenu(groupTree(nested)[0]),
        )
    }

    @Test fun theCountFollowsTheChoice() {
        val tree = groupTree(nested)
        assertEquals(39, groupChannelCount(tree, null))
        assertEquals(30, groupChannelCount(tree, "Sports"))
        assertEquals(8, groupChannelCount(tree, "Sports / Hockey"))
        assertEquals(0, groupChannelCount(tree, "Gone"))
    }

    @Test fun aChoicePastTheChipsStillHasItsChipInTheRow() {
        val tree = groupTree((1..20).map { top("$it", "Group ${it.toString().padStart(2, '0')}", it) })
        assertEquals(12, groupChips(tree, null).size)
        assertEquals(12, groupChips(tree, "Group 03").size)
        assertEquals("Group 19", groupChips(tree, "Group 19").last().name)
        assertEquals(13, groupChips(tree, "Group 19").size)
    }
}
