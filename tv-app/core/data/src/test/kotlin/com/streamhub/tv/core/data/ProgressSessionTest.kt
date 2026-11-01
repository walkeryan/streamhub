package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.boundProgressRequest
import com.streamhub.tv.core.data.repo.bindProgressSession
import io.ktor.client.plugins.auth.AuthCircuitBreaker
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.http.HttpHeaders
import com.streamhub.tv.core.data.repo.OwnedProgressJobs
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ProgressSessionTest {
    private val owner=Session("https://old.fixture.invalid","old-access","old-refresh",Profile("one","One"),"session-one")
    private val report=ProgressReport("movie",positionMs=9000,state=PlaybackState.PLAYING)
    @Test fun delayedOldProgressDropsAfterProfileServerOrLoginChange() {
        assertNull(boundProgressRequest(owner,owner.copy(profile=Profile("two","Two")),report))
        assertNull(boundProgressRequest(owner,owner.copy(serverUrl="https://other.fixture.invalid"),report))
        assertNull(boundProgressRequest(owner,owner.copy(generation="new-login"),report))
        assertNull(boundProgressRequest(owner,owner.copy(accessToken=null),report))
    }
    @Test fun normalRefreshKeepsOwnerAndNewAccessWhileDestinationAndExpectedProfileRemainPinned() {
        val refreshed=owner.copy(accessToken="refreshed-access",refreshToken="refreshed-refresh")
        val request=boundProgressRequest(owner,refreshed,report.copy(expectedProfileId="wrong-caller-value"))!!
        assertEquals("https://old.fixture.invalid/api/v1/playback/progress",request.url)
        assertEquals("refreshed-access",request.accessToken)
        assertEquals("one",request.body.expectedProfileId)
        assertFalse(request.toString().contains("refreshed-access"))
    }
    @Test fun capturedRequestCannotFollowSubsequentMutableSessionSwitch() {
        var current=owner
        val request=boundProgressRequest(owner,current,report)!!
        current=owner.copy(serverUrl="https://other.fixture.invalid",profile=Profile("two","Two"),accessToken="new-user-access",generation="other")
        assertNull(boundProgressRequest(owner,current,report))
        assertEquals("old-access",request.accessToken)
        assertEquals("one",request.body.expectedProfileId)
        assertTrue(request.url.startsWith("https://old.fixture.invalid/"))
    }
    @Test fun transportPinsAuthorizationAndDisablesAutomaticBearerReplacementOrRetry() {
        val request=boundProgressRequest(owner,owner,report)!!
        val builder=HttpRequestBuilder().apply {bindProgressSession(request)}
        assertTrue(builder.attributes.contains(AuthCircuitBreaker))
        assertEquals("Bearer old-access",builder.headers[HttpHeaders.Authorization])
        assertEquals("one",(builder.body as ProgressReport).expectedProfileId)
    }

    @Test fun delayedOwnedJobsCancelOnSwitchAndTeardownButSurviveRefresh()=runTest {
        val jobs=OwnedProgressJobs();var writes=0
        val old=launch {delay(100);writes++};jobs.track(old,owner)
        jobs.retainFor(owner.copy(profile=Profile("two","Two")))
        advanceUntilIdle();assertEquals(0,writes)
        val fresh=launch {delay(100);writes++};jobs.track(fresh,owner)
        jobs.retainFor(owner.copy(accessToken="rotated-access",refreshToken="rotated-refresh"))
        advanceUntilIdle();assertEquals(1,writes)
        val stopped=launch {delay(100);writes++};jobs.track(stopped,owner)
        jobs.cancelAll();advanceUntilIdle();assertEquals(1,writes)
    }

}
