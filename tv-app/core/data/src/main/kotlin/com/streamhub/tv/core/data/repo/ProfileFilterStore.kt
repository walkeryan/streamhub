package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import javax.inject.Inject
import javax.inject.Singleton

/** Read/merge our namespace; reject work queued for a server/profile that is no longer active. */
@Singleton
class ProfileFilterStore @Inject constructor(private val api: StreamHubApi, private val session: SessionStore) {
    private val mutex = Mutex()
    private suspend fun ensureOwner(owner: CatalogScope, profileId: String) {
        if (owner.profileId != profileId || session.current().catalogScope() != owner) {
            throw CancellationException("Preference scope changed")
        }
    }
    suspend fun read(profileId: String, key: String): JsonElement? {
        val owner = session.current().catalogScope()
        return mutex.withLock {
            ensureOwner(owner, profileId)
            val result = api.profilePrefs(profileId)[key]
            ensureOwner(owner, profileId)
            result
        }
    }
    suspend fun write(profileId: String, key: String, value: JsonElement): JsonObject {
        val owner = session.current().catalogScope()
        return mutex.withLock {
            ensureOwner(owner, profileId)
            val latest = api.profilePrefs(profileId)
            ensureOwner(owner, profileId)
            api.putProfilePrefs(profileId, mergeProfileFilter(latest, key, value))
        }
    }
}

internal fun mergeProfileFilter(latest: JsonObject, key: String, value: JsonElement): JsonObject =
    JsonObject(latest + (key to value))
