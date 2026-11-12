package com.streamhub.tv.core.data.api

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ShouldClearSessionTest {
    @Test fun rejectedRefreshTokenEndsTheSession() {
        listOf(400, 401, 403).forEach { assertTrue("status $it", shouldClearSession(it)) }
    }

    @Test fun serverRestartAndProxyErrorsKeepTheSession() {
        listOf(500, 502, 503, 504).forEach { assertFalse("status $it", shouldClearSession(it)) }
    }

    @Test fun otherClientErrorsKeepTheSession() {
        // 404 while a reverse proxy has no upstream yet, 408/429 under load: none of them say the token is bad.
        listOf(404, 408, 409, 429).forEach { assertFalse("status $it", shouldClearSession(it)) }
    }

    @Test fun noResponseKeepsTheSession() = assertFalse(shouldClearSession(null))
}
