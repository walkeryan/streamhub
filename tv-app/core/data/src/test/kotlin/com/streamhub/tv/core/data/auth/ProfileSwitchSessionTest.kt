package com.streamhub.tv.core.data.auth

import com.streamhub.tv.core.data.model.Profile
import org.junit.Assert.*
import org.junit.Test

class ProfileSwitchSessionTest {
    private val owner=Session("https://fixture.invalid","access","refresh",Profile("one","One"))
    @Test fun sameProfileAndServerAreNotEnoughForStaleChallenge() {
        assertTrue(sameSwitchSession(owner,owner.copy(accessToken="refreshed-access")))
        assertFalse(sameSwitchSession(owner,owner.copy(refreshToken="new-session")))
        assertFalse(sameSwitchSession(owner,owner.copy(serverUrl="https://other.invalid")))
        assertFalse(sameSwitchSession(owner,owner.copy(profile=Profile("two","Two"))))
        assertFalse(sameSwitchSession(owner.copy(refreshToken=null),owner.copy(refreshToken=null)))
    }
    @Test fun guardedRotationPreservesChallengeButNewLoginAndStaleRefreshDoNot() {
        val established=owner.copy(generation="session-one")
        val rotated=established.copy(accessToken="next-access",refreshToken="next-refresh")
        assertTrue(sameSwitchSession(established,rotated))
        assertFalse(sameSwitchSession(established,rotated.copy(generation="new-login")))
        assertTrue(canApplyRefresh(established,established,established.profile!!.id))
        assertFalse(canApplyRefresh(established,rotated,established.profile!!.id))
        assertFalse(canApplyRefresh(established,established,"wrong-target"))
        assertFalse(canApplyRefresh(established,established.copy(generation="new-login"),established.profile!!.id))
        val migrated=owner.copy(generation=switchSessionGeneration(owner),refreshToken="rotated-legacy")
        assertTrue(sameSwitchSession(owner,migrated))
    }

}
