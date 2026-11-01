package com.streamhub.tv.feature.search

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow

internal data class SearchScope(val server: String?, val profileId: String?, val preferredLanguages: List<String>)
internal data class SearchRequest(val scope: SearchScope, val query: String, val language: String?)
internal data class SearchLoad<T>(val request: SearchRequest?, val loading: Boolean = false, val result: Result<T>? = null)

/** Scope/query changes clear old results immediately, then debounce the replacement request. */
@OptIn(ExperimentalCoroutinesApi::class)
internal fun <T> searchLoads(requests: Flow<SearchRequest?>, load: suspend (SearchRequest) -> T): Flow<SearchLoad<T>> =
    requests.distinctUntilChanged().flatMapLatest { request -> flow {
        if (request == null || request.scope.profileId == null || request.scope.server == null || request.query.length < 2) {
            emit(SearchLoad<T>(request))
        } else {
            emit(SearchLoad<T>(request, loading = true))
            delay(400)
            val result = try { Result.success(load(request)) }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { Result.failure(error) }
            emit(SearchLoad(request, result = result))
        }
    } }
