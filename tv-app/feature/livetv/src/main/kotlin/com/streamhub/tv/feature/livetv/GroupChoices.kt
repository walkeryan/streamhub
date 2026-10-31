package com.streamhub.tv.feature.livetv

import com.streamhub.tv.core.data.model.ChannelGroup

internal data class GroupChoice(val name: String, val channelCount: Int)

/** The API selects by group name across sources, so identical names must share one count. */
internal fun groupChoices(groups: List<ChannelGroup>, query: String = ""): List<GroupChoice> = groups
    .asSequence()
    .filter { !it.hidden && it.name.contains(query.trim(), ignoreCase = true) }
    .groupBy { it.name }
    .map { (name, members) -> GroupChoice(name, members.sumOf { it.channelCount }) }
    .sortedBy { it.name.lowercase() }
