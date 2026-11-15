package com.streamhub.tv.shell

import com.streamhub.tv.core.data.repo.UpdateInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdatePromptRulesTest {
    private val build = UpdateInfo(14, "1.2.7", "https://hub.example/downloads/tv.apk", "a".repeat(64))

    @Test fun `checks once the web app is up, never over the player, then again after six hours`() {
        assertTrue(UpdatePromptRules.dueToCheck(onWebApp = true, playerOpen = false, lastCheckMs = null, nowMs = 1_000))
        assertFalse(UpdatePromptRules.dueToCheck(onWebApp = false, playerOpen = false, lastCheckMs = null, nowMs = 1_000))
        assertFalse(UpdatePromptRules.dueToCheck(onWebApp = true, playerOpen = true, lastCheckMs = null, nowMs = 1_000))
        assertFalse(UpdatePromptRules.dueToCheck(true, false, lastCheckMs = 1_000, nowMs = 1_000 + UpdatePromptRules.RECHECK_MS - 1))
        assertTrue(UpdatePromptRules.dueToCheck(true, false, lastCheckMs = 1_000, nowMs = 1_000 + UpdatePromptRules.RECHECK_MS))
    }

    @Test fun `a build answered Later is not offered again this run, a newer one is`() {
        assertEquals(build, UpdatePromptRules.offer(build, declinedVersionCode = null))
        assertNull(UpdatePromptRules.offer(build, declinedVersionCode = 14))
        assertEquals(build, UpdatePromptRules.offer(build, declinedVersionCode = 13))
        assertNull(UpdatePromptRules.offer(null, declinedVersionCode = null))
    }
}
