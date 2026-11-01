package com.streamhub.tv.feature.home

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ProfileHomeLoadTest {
    @Test fun switchingProfileCancelsOldResponseAndLogoutClearsData() = runTest {
        val profiles = MutableStateFlow<String?>("one")
        val refresh = MutableStateFlow(0)
        val pending = CompletableDeferred<String>()
        var cancelled = false
        val values = mutableListOf<ProfileHomeLoad<String>>()
        val job = backgroundScope.launch {
            profileHomeLoads(profiles, refresh) { profile ->
                if (profile == "one") try { pending.await() } finally { cancelled = true }
                else "second profile history"
            }.collect { values += it }
        }
        runCurrent()
        profiles.value = "two"
        runCurrent()
        assertTrue(cancelled)
        assertEquals("two", values.last().profileId)
        assertEquals("second profile history", values.last().result?.getOrNull())
        pending.complete("first profile history")
        runCurrent()
        assertFalse(values.any { it.result?.getOrNull() == "first profile history" })
        profiles.value = null
        runCurrent()
        assertNull(values.last().profileId)
        assertNull(values.last().result)
        job.cancel()
    }

    @Test fun refreshCancelsOlderLoadWithoutPublishingCancellationAsError() = runTest {
        val profiles = MutableStateFlow<String?>("one")
        val refresh = MutableStateFlow(0)
        var calls = 0
        val pending = CompletableDeferred<String>()
        val values = mutableListOf<ProfileHomeLoad<String>>()
        backgroundScope.launch {
            profileHomeLoads(profiles, refresh) { if (++calls == 1) pending.await() else "fresh history" }.collect { values += it }
        }
        runCurrent()
        refresh.value++
        runCurrent()
        assertEquals("fresh history", values.last().result?.getOrNull())
        assertFalse(values.any { it.result?.isFailure == true })
    }
}
