package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.api.ApiException
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SecureProfileSwitchTest {
    private val enabled=AuthConfig(googleRequired=true,contentPolicyEnabled=true,ownerPinConfigured=true)
    private val child=Profile("kid","Kid",contentRestricted=true)
    private val admin=Profile("owner","Owner",isAdmin=true,hasPin=true)
    private val challenge=OwnerPinChallenge("fixture-challenge","fixture-private-poll",10)
    private val tokens=AuthTokens("fixture-access","fixture-refresh",admin)
    @Test fun restrictedExitAlwaysRequiresDedicatedOwnerPinNeverTargetPinOrAdminFlag() {
        assertEquals(ProfileSwitchPath.OwnerPin,profileSwitchPath(enabled,child,admin))
        assertEquals(ProfileSwitchPath.OwnerPin,profileSwitchPath(enabled,child.copy(isAdmin=true),admin))
        assertEquals(ProfileSwitchPath.SameProfile,profileSwitchPath(enabled,child,child))
    }
    @Test fun disabledGoogleModeNeverFallsBackToLocalLogin() {
        assertEquals(ProfileSwitchPath.Unavailable,profileSwitchPath(AuthConfig(googleRequired=true),child,admin))
        assertEquals(ProfileSwitchPath.Unavailable,profileSwitchPath(null,child,admin))
        assertEquals(ProfileSwitchPath.Legacy,profileSwitchPath(AuthConfig(googleRequired=false),child,admin))
    }
    @Test fun unprotectedTargetsHaveNoCredentialEscape() {
        val user=Profile("user","User")
        assertEquals(ProfileSwitchPath.OwnerReauthRequired,profileSwitchPath(enabled,user,Profile("other","Other")))
        assertEquals(ProfileSwitchPath.ExistingPin,profileSwitchPath(enabled,user,admin))
        assertEquals(ProfileSwitchPath.Secure,profileSwitchPath(enabled,admin,user))
    }
    @Test fun exactChallengeTargetAndDigitsRequired()=runTest {
        var calls=0
        val result=verifyOwnerPinBound(challenge,admin.id,"123456",{true}) { request ->
            calls++;assertEquals(challenge.challengeId,request.challengeId);assertEquals(challenge.pollToken,request.pollToken);tokens
        }
        assertEquals(tokens,result);assertEquals(1,calls)
        listOf("12345","1234567890123","１２３４５６","123 56").forEach {assertFalse(validOwnerPin(it))}
        assertTrue(validOwnerPin("123456789012"))
    }
    @Test fun cancelBackRejectsLateApprovalAndCancelsExactlyOnce()=runTest {
        var accepted=false;var cancels=0
        val job=launch {
            awaitOwnerPin(challenge,admin.id,{"123456"},{true},verify={withContext(NonCancellable){delay(100)};tokens},rejected={},cancel={cancels++})
            accepted=true
        }
        runCurrent();job.cancel();job.cancel();advanceUntilIdle()
        assertFalse(accepted);assertEquals(1,cancels)
    }
    @Test fun changedSessionAndWrongTargetCannotCommit()=runTest {
        var current=true
        try {verifyOwnerPinBound(challenge,admin.id,"123456",{current}){current=false;tokens};fail("stale session")}catch(_:CancellationException){}
        try {verifyOwnerPinBound(challenge,child.id,"123456",{true}){tokens};fail("wrong target")}catch(_:IllegalArgumentException){}
    }
    @Test fun expiredInputWaitCancelsWithoutVerification()=runTest {
        var calls=0;var cancels=0
        try {awaitOwnerPin(challenge.copy(expiresIn=1),admin.id,{delay(2000);"123456"},{true},{calls++;tokens},{},{cancels++});fail("expired")}
        catch(_:TimeoutCancellationException){}
        assertEquals(0,calls);assertEquals(1,cancels)
    }
    @Test fun rateLimitWaitsAndRequiresNewInputNeverReplaysSecret()=runTest {
        var inputs=0;var calls=0;var seconds=0
        val result=awaitOwnerPin(challenge,admin.id,{inputs++;"123456"},{true},verify={
            if(++calls==1)throw ApiException(429,null,"rate limited",2)
            tokens
        },rejected={seconds=it},cancel={fail("success should consume")})
        assertEquals(tokens,result);assertEquals(2,inputs);assertEquals(2,calls);assertEquals(2,seconds);assertEquals(2000,testScheduler.currentTime)
    }
    @Test fun repeatedSelectCannotQueuePinWhileVerificationRuns()=runTest {
        val input=Channel<String>(Channel.RENDEZVOUS);var calls=0
        val job=launch {awaitOwnerPin(challenge,admin.id,{input.receive()},{true},{calls++;delay(100);tokens},{},{})}
        runCurrent();assertTrue(input.trySend("123456").isSuccess)
        assertFalse(input.trySend("123456").isSuccess)
        advanceUntilIdle();job.join();assertEquals(1,calls);input.close()
    }
    @Test fun arbitraryFailureDoesNotAutomaticallyRetry()=runTest {
        var calls=0;var cancels=0
        try {awaitOwnerPin(challenge,admin.id,{"123456"},{true},{calls++;throw ApiException(410,null,"expired")},{},{cancels++});fail("expired")}
        catch(_:ApiException){}
        assertEquals(1,calls);assertEquals(1,cancels)
        assertEquals(60,ownerPinRetryDelay(null));assertEquals(1,ownerPinRetryDelay(-1))
    }
    @Test fun cancellationNeverSendsChallengeSecretToChangedSessionOrServer()=runTest {
        var current=true;var cancels=0
        try {awaitOwnerPin(challenge,admin.id,{"123456"},{current},{current=false;tokens},{},{cancels++});fail("stale")}
        catch(_:CancellationException){}
        assertEquals(0,cancels)
    }

}
