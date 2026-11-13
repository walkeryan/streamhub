package com.streamhub.tv.core.ui.focus

import org.junit.Assert.assertEquals
import org.junit.Test

class BackPolicyTest {
    private fun decide(route: String?, topLevel: Boolean = true, rail: Boolean = false, last: Long = 0, now: Long = 10_000) =
        BackPolicy.decide(route, topLevel, rail, last, now)

    @Test fun topLevelContentOpensTheRail() {
        listOf("live", "search", "browse/{section}", "settings").forEach { assertEquals(it, BackAction.OpenRail, decide(it)) }
    }

    @Test fun openRailGoesHome() = assertEquals(BackAction.GoHome, decide("live", rail = true))

    @Test fun homeAsksBeforeExiting() {
        assertEquals(BackAction.ConfirmExit, decide(BackPolicy.HOME_ROUTE))
        // The rail being open on Home changes nothing: Home is already the last rung.
        assertEquals(BackAction.ConfirmExit, decide(BackPolicy.HOME_ROUTE, rail = true))
    }

    @Test fun secondBackOnHomeWithinTheWindowExits() {
        assertEquals(BackAction.Exit, decide(BackPolicy.HOME_ROUTE, last = 9_000, now = 10_000))
        assertEquals(BackAction.Exit, decide(BackPolicy.HOME_ROUTE, last = 8_000, now = 10_000))
    }

    @Test fun confirmationExpiresAfterTwoSeconds() {
        assertEquals(BackAction.ConfirmExit, decide(BackPolicy.HOME_ROUTE, last = 7_999, now = 10_000))
        // A clock that went backwards must not exit either.
        assertEquals(BackAction.ConfirmExit, decide(BackPolicy.HOME_ROUTE, last = 11_000, now = 10_000))
    }

    @Test fun fullLadderNeverLoops() {
        // content → rail → Home → confirm → exit: four presses from any top-level screen.
        assertEquals(BackAction.OpenRail, decide("favorites"))
        assertEquals(BackAction.GoHome, decide("favorites", rail = true))
        assertEquals(BackAction.ConfirmExit, decide(BackPolicy.HOME_ROUTE, now = 20_000))
        assertEquals(BackAction.Exit, decide(BackPolicy.HOME_ROUTE, last = 20_000, now = 20_600))
    }

    @Test fun deeperScreensKeepTheDefault() {
        assertEquals(BackAction.Default, decide("details/{id}", topLevel = false))
        assertEquals(BackAction.Default, decide("details/{id}", topLevel = false, rail = true))
        assertEquals(BackAction.Default, decide(null))
    }
}
