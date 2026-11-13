package com.streamhub.tv.core.data.repo

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import javax.inject.Inject
import javax.inject.Singleton

/** App-wide auth events (refresh failed → go back to pairing). */
@Singleton
class SessionEvents @Inject constructor() {
    private val _expired = MutableSharedFlow<Unit>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val expired: SharedFlow<Unit> = _expired
    fun emitExpired() { _expired.tryEmit(Unit) }

    /**
     * The token pair was exchanged after a 401. The server answers 401 `session_stale` when a role,
     * permission or limit changed, so whoever caches what the user may do (MeRepository) reads it again.
     */
    private val _refreshed = MutableSharedFlow<Unit>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val refreshed: SharedFlow<Unit> = _refreshed
    fun emitRefreshed() { _refreshed.tryEmit(Unit) }
}
