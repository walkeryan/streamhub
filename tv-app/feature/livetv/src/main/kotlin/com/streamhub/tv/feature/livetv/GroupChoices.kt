package com.streamhub.tv.feature.livetv

import com.streamhub.tv.core.data.model.ChannelGroup

/** One row of the "More groups" list. [path] is what selects it (`Parent / Child`, or a top-level name). */
internal data class GroupChoice(val name: String, val channelCount: Int, val path: String = name)

/**
 * Every visible group by its path, for the searchable list. The API selects by path across sources,
 * so identical paths share one row and one count.
 */
internal fun groupChoices(groups: List<ChannelGroup>, query: String = ""): List<GroupChoice> = groups
    .asSequence()
    .filter { !it.hidden && it.groupPath.contains(query.trim(), ignoreCase = true) }
    .groupBy { it.groupPath }
    .map { (path, members) -> GroupChoice(path, members.sumOf { it.channelCount }, path) }
    .sortedBy { it.name.lowercase() }

/** A child of a top-level group: [name] is its own name, [path] what selects it. */
internal data class GroupChild(val name: String, val path: String, val channelCount: Int)

/** A top-level group and its children, in the server's order. A parent's count includes its children's. */
internal data class GroupNode(val name: String, val channelCount: Int, val children: List<GroupChild> = emptyList()) {
    /** A top-level group is selected by its name. */
    val path: String get() = name
}

/**
 * The two levels of `GET /channels/groups` as the chips show them: one node per top-level name (the
 * same name in two sources is one chip, as the server's `group=` filter treats it), each with its
 * children. Hidden groups are left out. A child whose parent row is missing still gets its parent,
 * counted from the children, so nothing the server lists is unreachable.
 */
internal fun groupTree(groups: List<ChannelGroup>): List<GroupNode> {
    val visible = groups.filterNot { it.hidden }
    val order = LinkedHashMap<String, Unit>()
    val own = HashMap<String, Int>()
    val children = HashMap<String, LinkedHashMap<String, GroupChild>>()
    for (g in visible) {
        val parent = g.parent?.takeIf { it.isNotBlank() }
        if (parent == null) {
            order[g.name] = Unit
            own[g.name] = (own[g.name] ?: 0) + g.channelCount
        } else {
            order[parent] = Unit
            val under = children.getOrPut(parent) { LinkedHashMap() }
            val path = g.path.ifBlank { "$parent / ${g.name}" }
            val seen = under[path]
            under[path] = GroupChild(g.name, path, (seen?.channelCount ?: 0) + g.channelCount)
        }
    }
    return order.keys.map { name ->
        val below = children[name]?.values?.toList().orEmpty()
        GroupNode(name, own[name] ?: below.sumOf { it.channelCount }, below)
    }
}

/** The node whose chip stands for [selected]: the group itself, or the parent of the chosen child. */
internal fun List<GroupNode>.nodeFor(selected: String?): GroupNode? =
    if (selected == null) null else firstOrNull { it.path == selected } ?: firstOrNull { node -> node.children.any { it.path == selected } }

/** What a chip reads: the group, or `Parent · Child` while one of its children is the choice. */
internal fun groupChipLabel(node: GroupNode, selected: String?): String =
    node.children.firstOrNull { it.path == selected }?.let { "${node.name} · ${it.name}" } ?: node.name

/** How many channels the choice in force lists; every top-level group when there is none. */
internal fun groupChannelCount(tree: List<GroupNode>, selected: String?): Int {
    if (selected == null) return tree.sumOf { it.channelCount }
    val node = tree.nodeFor(selected) ?: return 0
    return node.children.firstOrNull { it.path == selected }?.channelCount ?: node.channelCount
}

/**
 * The chips of the row: the first [limit] top-level groups, and the one in force when it lies past
 * them, so the choice is always named in the row. The rest are reached through "More groups".
 */
internal fun groupChips(tree: List<GroupNode>, selected: String?, limit: Int = GROUP_CHIP_LIMIT): List<GroupNode> {
    val head = tree.take(limit)
    val chosen = tree.nodeFor(selected)
    return if (chosen == null || chosen in head) head else head + chosen
}

internal const val GROUP_CHIP_LIMIT = 12

/** The entries of a parent's list: everything in it first, then each child with its count. */
internal data class GroupMenuEntry(val path: String, val label: String, val channelCount: Int)
internal fun groupMenu(node: GroupNode): List<GroupMenuEntry> =
    listOf(GroupMenuEntry(node.path, "All ${node.name}", node.channelCount)) + node.children.map { GroupMenuEntry(it.path, it.name, it.channelCount) }
