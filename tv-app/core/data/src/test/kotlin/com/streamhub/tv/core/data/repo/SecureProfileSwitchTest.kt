package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SecureProfileSwitchTest {
    private val enabled=AuthConfig(googleRequired=true,contentPolicyEnabled=true)
    private val child=Profile("kid","Kid",contentRestricted=true)
    private val admin=Profile("owner","Owner",isAdmin=true,hasPin=true)
    private val challenge=OwnerReauthChallenge("fixture-challenge","fixture-private-poll","https://fixture.invalid/owner-verify?challenge_id=fixture-challenge",10,1)
    private val tokens=AuthTokens("fixture-access","fixture-refresh",admin)
    @Test fun restrictedExitAlwaysRequiresGoogleNeverTargetPinOrAdminFlag() {
        assertEquals(ProfileSwitchPath.OwnerGoogle,profileSwitchPath(enabled,child,admin))
        assertEquals(ProfileSwitchPath.OwnerGoogle,profileSwitchPath(enabled,child.copy(isAdmin=true),admin))
        assertEquals(ProfileSwitchPath.SameProfile,profileSwitchPath(enabled,child,child))
    }
    @Test fun disabledGoogleModeNeverFallsBackToLocalLogin() {
        assertEquals(ProfileSwitchPath.Unavailable,profileSwitchPath(AuthConfig(googleRequired=true),child,admin))
        assertEquals(ProfileSwitchPath.Unavailable,profileSwitchPath(null,child,admin))
        assertEquals(ProfileSwitchPath.Legacy,profileSwitchPath(AuthConfig(googleRequired=false),child,admin))
    }
    @Test fun unprotectedTargetsHaveNoPinEscape() {
        val user=Profile("user","User")
        assertEquals(ProfileSwitchPath.OwnerReauthRequired,profileSwitchPath(enabled,user,Profile("other","Other")))
        assertEquals(ProfileSwitchPath.OwnerReauthRequired,profileSwitchPath(enabled,user,admin.copy(hasPin=false,hasPassword=true)))
        assertEquals(ProfileSwitchPath.ExistingPin,profileSwitchPath(enabled,user,admin))
        assertEquals(ProfileSwitchPath.Secure,profileSwitchPath(enabled,admin,user))
    }
    @Test fun pendingPollPreservesExactChallengeAndTarget()=runTest {
        var polls=0
        val result=awaitOwnerReauth(challenge,admin.id,{true}, poll={ request ->
            assertEquals(challenge.challengeId,request.challengeId)
            assertEquals(challenge.pollToken,request.pollToken)
            if(++polls<3)null else tokens
        })
        assertEquals(tokens,result);assertEquals(3,polls)
    }
    @Test fun cancelBackRejectsLateApproval()=runTest {
        var accepted=false
        val job=launch {
            awaitOwnerReauth(challenge,admin.id,{true}, poll={withContext(NonCancellable){delay(100)};tokens})
            accepted=true
        }
        runCurrent();job.cancel();advanceUntilIdle();assertFalse(accepted)
    }
    @Test fun changedSessionAndWrongTargetCannotCommit()=runTest {
        var current=true
        try {awaitOwnerReauth(challenge,admin.id,{current},poll={current=false;tokens});fail("shouldcancel")}catch(_:CancellationException){}
        try {awaitOwnerReauth(challenge,child.id,{true},poll={tokens});fail("wrongtarget")}catch(_:IllegalArgumentException){}
    }
    @Test fun expiredChallengeNeverReturnsTokens()=runTest {
        try {awaitOwnerReauth(challenge.copy(expiresIn=1),admin.id,{true},poll={null});fail("shouldexpire")}catch(_:TimeoutCancellationException){}
    }
    @Test fun repeatedCancelRevokesChallengeOnceWithoutAcceptingLateSuccess()=runTest {
        var cancels=0;var accepted=false
        val job=launch {
            awaitOwnerReauth(challenge,admin.id,{true},poll={withContext(NonCancellable){delay(100)};tokens},cancel={request->
                assertEquals(challenge.challengeId,request.challengeId);assertEquals(challenge.pollToken,request.pollToken);cancels++
            })
            accepted=true
        }
        runCurrent();job.cancel();job.cancel();advanceUntilIdle()
        assertFalse(accepted);assertEquals(1,cancels)
    }

}
