package com.streamhub.tv.feature.home

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf

internal data class ProfileHomeLoad<T>(val profileId: String?, val result: Result<T>?)

/** Profile changes/logout and refresh cancel previous loads before they can publish. */
@OptIn(ExperimentalCoroutinesApi::class)
internal fun <T> profileHomeLoads(profiles: Flow<String?>, refreshes: Flow<Int>, load: suspend (String) -> T): Flow<ProfileHomeLoad<T>> =
    profiles.distinctUntilChanged().flatMapLatest { profile ->
        if (profile == null) flowOf(ProfileHomeLoad<T>(null, null))
        else refreshes.flatMapLatest {
            flow {
                emit(ProfileHomeLoad<T>(profile, null))
                val result = try { Result.success(load(profile)) }
                catch (cancelled: CancellationException) { throw cancelled }
                catch (error: Exception) { Result.failure(error) }
                emit(ProfileHomeLoad(profile, result))
            }
        }
    }
