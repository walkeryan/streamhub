package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.StreamHubApi
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import javax.inject.Inject
import javax.inject.Singleton

/** Read/merge our namespace so other TV/web preferences survive each save. */
@Singleton
class ProfileFilterStore @Inject constructor(private val api: StreamHubApi) {
    private val mutex = Mutex()
    suspend fun read(profileId: String, key: String): JsonElement? = mutex.withLock { api.profilePrefs(profileId)[key] }
    suspend fun write(profileId: String, key: String, value: JsonElement) = mutex.withLock {
        val latest = api.profilePrefs(profileId)
        api.putProfilePrefs(profileId, mergeProfileFilter(latest, key, value))
    }
}

internal fun mergeProfileFilter(latest: JsonObject, key: String, value: JsonElement): JsonObject =
    JsonObject(latest + (key to value))
