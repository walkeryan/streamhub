package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.data.model.ChannelList
import kotlinx.coroutines.CancellationException
import javax.inject.Inject
import javax.inject.Singleton

/** Ordered playback lists are scoped to the paired server and current profile. */
@Singleton
class ChannelListCache @Inject constructor(private val api: StreamHubApi, private val session: SessionStore) {
    private val lists = LinkedHashMap<Pair<String, String>, List<Channel>>()
    private var lastKey: Pair<String, String>? = null

    fun clear() = synchronized(lists) { lists.clear(); lastKey=null }

    private suspend fun scope(): String {
        val s = session.current()
        return "${s.apiBase}|${s.profile?.id}"
    }

    private suspend fun put(list: ChannelList, owner: String): ChannelList {
        if (scope() != owner) throw CancellationException("Profile changed")
        if (list.listId.isNotBlank()) synchronized(lists) {
            val key = owner to list.listId
            lists[key] = list.channels
            lastKey = key
            while (lists.size > 32) lists.remove(lists.keys.first())
        }
        return list
    }

    /** Existing callers remain unpaged; Guide explicitly requests a bounded page. */
    suspend fun fetch(
        group: String? = null,
        languages: List<String>? = null,
        favorites: Boolean? = null,
        q: String? = null,
        hasEpg: Boolean? = null,
        limit: Int? = null,
        offset: Int? = null,
        country: String? = null,
    ): ChannelList {
        val owner = scope()
        return put(api.channels(group = group, languages = languages, favorites = favorites,
            q = q, hasEpg = hasEpg, limit = limit, offset = offset, country = country), owner)
    }

    suspend fun resolve(listId: String?, currentChannelId: String): List<Channel> {
        val owner = scope()
        synchronized(lists) { lists[owner to listId.orEmpty()] }?.let { return it }
        if (listId != null) {
            try {
                return put(api.channels(listId = listId), owner).channels
            } catch (e: CancellationException) { throw e } catch (_: Exception) { /* expired server list */ }
        }
        synchronized(lists) {
            lastKey?.takeIf { it.first == owner }?.let { lists[it] }
        }?.takeIf { l -> l.any { it.id == currentChannelId } }?.let { return it }
        return try { fetch().channels } catch (e: CancellationException) { throw e } catch (_: Exception) { emptyList() }
    }
}
