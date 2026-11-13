package com.streamhub.tv.core.player

import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.HttpDataSource
import androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy
import androidx.media3.exoplayer.upstream.LoadErrorHandlingPolicy
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamRefusals

/**
 * The server's refusal behind a player failure, if that is what it was. Media3 reports a non-2xx
 * answer as an [HttpDataSource.InvalidResponseCodeException] somewhere in the cause chain, with
 * the status and the body the server sent; [StreamRefusals] reads those.
 */
@OptIn(UnstableApi::class)
internal fun streamRefusal(error: Throwable?): ApiException? {
    var cause = error
    // A cause chain is a few links long; the bound only guards against one that loops.
    repeat(MAX_CAUSE_DEPTH) {
        val e = cause ?: return null
        if (e is HttpDataSource.InvalidResponseCodeException) return StreamRefusals.parse(e.responseCode, e.responseBody)
        cause = e.cause
    }
    return null
}

private const val MAX_CAUSE_DEPTH = 8

/**
 * The default policy asks for a failed playlist or segment up to six more times, seconds apart. A
 * refusal from the server will be the same answer every time, so it is reported at once: the
 * viewer hears why, and a session that only needs refreshing is refreshed 15 s sooner.
 */
@OptIn(UnstableApi::class)
internal class RefusalAwareLoadErrorPolicy(minimumLoadableRetryCount: Int) : DefaultLoadErrorHandlingPolicy(minimumLoadableRetryCount) {
    override fun getRetryDelayMsFor(loadErrorInfo: LoadErrorHandlingPolicy.LoadErrorInfo): Long =
        if (streamRefusal(loadErrorInfo.exception) != null) C.TIME_UNSET else super.getRetryDelayMsFor(loadErrorInfo)
}
