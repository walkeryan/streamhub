package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.*
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AuthorizationCommitGateTest {
    private val challenge=OwnerPinChallenge("fixture-challenge","fixture-poll",300)
    private val tokens=AuthTokens("fixture-access","fixture-refresh",Profile("owner","Owner"))
    @Test fun backBeforePostCancelsButBackWhilePostPendingCannotDiscardCommit()=runTest {
        val gate=AuthorizationCommitGate()
        var beforeCancelled=false
        assertTrue(gate.cancelIfIdle {beforeCancelled=true});assertTrue(beforeCancelled)
        val response=CompletableDeferred<AuthTokens>()
        var postCalls=0;var committed:AuthTokens?=null;var cancels=0
        val job=launch {
            awaitOwnerPin(challenge,"owner",{"123456"},{true},verify={postCalls++;response.await()},rejected={},cancel={cancels++},
                authorize={gate.finish(it)},complete={committed=it})
        }
        runCurrent();assertEquals(1,postCalls);assertTrue(gate.finishing)
        assertFalse(gate.cancelIfIdle {job.cancel()})
        assertFalse(gate.cancelIfIdle {job.cancel()})
        response.complete(tokens);advanceUntilIdle();job.join()
        assertEquals(tokens,committed);assertEquals(0,cancels);assertFalse(gate.finishing)
    }
    @Test fun forcedScreenDisposalStillCommitsReturnedTokensInsideProtectedPhase()=runTest {
        val gate=AuthorizationCommitGate();val response=CompletableDeferred<AuthTokens>()
        var committed:AuthTokens?=null
        val job=launch {gate.finish {val result=response.await();committed=result}}
        runCurrent();job.cancel();response.complete(tokens);advanceUntilIdle()
        assertEquals(tokens,committed);assertFalse(gate.finishing)
    }
    @Test fun postTimeoutIsUncertainWithoutReplayOrChallengeCancellation()=runTest {
        val gate=AuthorizationCommitGate();var calls=0;var inputs=0;var cancels=0;var committed=false
        try {
            awaitOwnerPin(challenge,"owner",{inputs++;"123456"},{true},verify={calls++;delay(21_000);tokens},rejected={},cancel={cancels++},
                authorize={gate.finish(it)},complete={committed=true})
            fail("must report uncertain")
        } catch(_:AuthorizationOutcomeUnknown){}
        assertEquals(20_000,testScheduler.currentTime);assertEquals(1,calls);assertEquals(1,inputs)
        assertEquals(0,cancels);assertFalse(committed);assertFalse(gate.finishing)
    }
    @Test fun directProfileSwitchUsesSameProtectedCommitAndTransportFailureIsUncertain()=runTest {
        val gate=AuthorizationCommitGate();var committed=false
        val result=gate.finish {delay(100);committed=true;tokens}
        assertTrue(committed);assertEquals(tokens,result)
        try {gate.finish {throw java.io.IOException("fixture connection lost")};fail("uncertain")}
        catch(_:AuthorizationOutcomeUnknown){}
        assertFalse(gate.finishing)
    }
}
