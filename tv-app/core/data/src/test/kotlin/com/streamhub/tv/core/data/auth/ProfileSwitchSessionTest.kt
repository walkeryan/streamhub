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
    @Test fun verificationQrMustBeCanonicalHttpsOriginNotMaliciousLink() {
        val path="/api/v1/auth/owner-reauth/verify?challenge_id=public"
        assertTrue(validOwnerVerificationUri(owner.serverUrl,"https://fixture.invalid$path"))
        assertFalse(validOwnerVerificationUri(owner.serverUrl,"https://attacker.invalid$path"))
        assertFalse(validOwnerVerificationUri(owner.serverUrl,"https://user:secret@fixture.invalid$path"))
        assertFalse(validOwnerVerificationUri(owner.serverUrl,"http://fixture.invalid$path"))
        assertFalse(validOwnerVerificationUri(owner.serverUrl,"https://fixture.invalid/other"))
    }

}
