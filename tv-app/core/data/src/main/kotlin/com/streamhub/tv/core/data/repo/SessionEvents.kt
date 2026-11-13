package com.streamhub.tv.core.data.repo

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import javax.inject.Inject
import javax.inject.Singleton

/** Why the session this TV holds stopped working. */
enum class SessionEnd {
    /** The refresh token was rejected without a reason of its own: the TV has to be paired again. */
    Expired,
    /** 401 `session_revoked`: signed out from another device, or the session changed hands. Pair again. */
    Revoked,
    /** 401 `user_suspended`: about the person, not the token. The pairing is kept; a reinstatement makes it work again. */
    Suspended;

    companion object {
        /** The end a server `code` on a 401 stands for; null for codes that do not end the session. */
        fun ofCode(code: String?): SessionEnd? = when (code) {
            "session_revoked" -> Revoked
            "user_suspended" -> Suspended
            else -> null
        }
    }
}

/** App-wide auth events. */
@Singleton
class SessionEvents @Inject constructor() {
    /**
     * The session ended. For [SessionEnd.Expired] and [SessionEnd.Revoked] the tokens are already
     * gone (the server address is kept) and the shell goes back to pairing; for
     * [SessionEnd.Suspended] nothing was cleared and the shell explains.
     */
    private val _ended = MutableSharedFlow<SessionEnd>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val ended: SharedFlow<SessionEnd> = _ended
    fun emitEnded(end: SessionEnd) { _ended.tryEmit(end) }

    /**
     * The token pair was exchanged after a 401. The server answers 401 `session_stale` when a role,
     * permission or limit changed, so whoever caches what the user may do (MeRepository) reads it again.
     */
    private val _refreshed = MutableSharedFlow<Unit>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val refreshed: SharedFlow<Unit> = _refreshed
    fun emitRefreshed() { _refreshed.tryEmit(Unit) }
}
