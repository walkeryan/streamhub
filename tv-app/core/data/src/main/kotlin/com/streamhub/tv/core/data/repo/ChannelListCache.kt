package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.data.model.ChannelList
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/**
 * PlaybackDescriptor.channel_context gives {list_id, index, count} but the contract has no
 * "GET /channels?list_id=" — so every screen that fetches /channels registers the result here
 * and the player zaps within it. Fallback: unfiltered /channels (profile default languages).
 */
@Singleton
class ChannelListCache @Inject constructor(private val api: StreamHubApi) {
    private val lists = ConcurrentHashMap<String, List<Channel>>()
    @Volatile private var lastListId: String? = null

    fun put(list: ChannelList) {
        if (list.listId.isNotBlank()) {
            lists[list.listId] = list.channels
            lastListId = list.listId
        }
    }

    /** Fetch /channels with the given filters and cache it. */
    suspend fun fetch(
        group: String? = null,
        languages: List<String>? = null,
        favorites: Boolean? = null,
    ): ChannelList = api.channels(group = group, languages = languages, favorites = favorites).also(::put)

    suspend fun resolve(listId: String?, currentChannelId: String): List<Channel> {
        listId?.let { lists[it] }?.let { return it }
        lastListId?.let { lists[it] }?.takeIf { l -> l.any { it.id == currentChannelId } }?.let { return it }
        return runCatching { fetch().channels }.getOrDefault(emptyList())
    }
}
