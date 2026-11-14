package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.model.ApiError

/**
 * Refusals the server gives on a media URL itself (`/stream/{token}`, its playlist and segments),
 * as opposed to on an API call: the player sees them as an HTTP status with the server's usual
 * error body. Read here from that status and body, so the same wording and the same reactions
 * apply as when POST /playback/resolve refuses.
 */
object StreamRefusals {
    /**
     * What ends playback of a stream for a reason that asking for the same address again cannot change:
     *  - 401 `session_revoked` | `user_suspended` | `session_stale`: the session, the user or the user
     *    version the stream was issued to no longer stands
     *  - 403 `remote_access_denied`: the user may only watch on the home network
     *  - 403 `content_restricted`: the user's rating ceiling changed and no longer admits the title
     *  - 409 `user_stream_limit`: the user's limit is now used by their other streams
     *  - 403 `stream_epoch_stale`: a segment of an earlier start of a watch room's shared stream, which a
     *    seek has since restarted. Asking for it again answers the same; the member's descriptor
     *    (GET /rooms/{code}/descriptor) says where the room's stream is now.
     *  - 403 `playback_paused`: the stream was issued before the server restarted, or an administrator
     *    stopped playback. The address is dead either way; a fresh resolve tells the two apart.
     */
    val CODES: Set<String> = setOf("session_revoked", "user_suspended", "session_stale", "remote_access_denied", "content_restricted", "user_stream_limit", STREAM_EPOCH_STALE, PLAYBACK_PAUSED)

    const val STREAM_EPOCH_STALE = "stream_epoch_stale"
    const val PLAYBACK_PAUSED = "playback_paused"

    /** Asking for the stream again is worth it once the session has been refreshed (or found to be over). */
    val ABOUT_THE_SESSION: Set<String> = setOf("session_revoked", "user_suspended", "session_stale")

    private val STATUSES = setOf(401, 403, 409)

    /** An error body is a few hundred bytes; anything much larger is not the server's own answer. */
    private const val MAX_BODY_BYTES = 64 * 1024

    /**
     * The refusal behind an HTTP answer to a media request, or null when it is not one of [CODES]:
     * another status, a body that is not the server's error JSON (a provider's own 403 page on a
     * direct stream), or a code that playback already handles in its own way (`source_busy`).
     */
    fun parse(status: Int, body: ByteArray?): ApiException? {
        if (status !in STATUSES || body == null || body.isEmpty() || body.size > MAX_BODY_BYTES) return null
        val error = runCatching { StreamHubJson.decodeFromString(ApiError.serializer(), body.toString(Charsets.UTF_8)) }.getOrNull() ?: return null
        if (error.code !in CODES) return null
        return ApiException(status, error, error.message.takeIf { it.isNotBlank() } ?: "HTTP $status")
    }
}
