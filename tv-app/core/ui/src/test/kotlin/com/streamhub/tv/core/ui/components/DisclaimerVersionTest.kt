package com.streamhub.tv.core.ui.components

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DisclaimerVersionTest {
    @Test fun notAcceptedWhenMissing() {
        assertFalse(isDisclaimerAccepted(null, "1.0"))
        assertFalse(isDisclaimerAccepted("", "1.0"))
    }

    @Test fun sameVersionIsAccepted() = assertTrue(isDisclaimerAccepted("1.0", "1.0"))

    @Test fun newerAcceptanceCoversOlderRequirement() {
        assertTrue(isDisclaimerAccepted("1.1", "1.0"))
        assertTrue(isDisclaimerAccepted("2", "1.9"))
        assertTrue(isDisclaimerAccepted("1.0.0", "1.0"))
    }

    @Test fun olderAcceptanceDoesNotCoverNewerRequirement() {
        assertFalse(isDisclaimerAccepted("1.0", "1.1"))
        assertFalse(isDisclaimerAccepted("0.9", "1.0"))
    }

    @Test fun nonNumericVersionsNeedAnExactMatch() {
        assertTrue(isDisclaimerAccepted("2026-10", "2026-10"))
        assertFalse(isDisclaimerAccepted("beta", "1.0"))
    }
}
