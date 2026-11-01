package com.streamhub.tv.core.player

import org.junit.Assert.*
import org.junit.Test

class AudioLanguagePolicyTest {
    @Test fun actualEnglishTracksAcceptAliasesButNeverProviderLabels() {
        listOf("en","eng","en-US"," EN_gb ").forEach {assertTrue(preferredAudioSelected(listOf("eng"),listOf(it)))}
        listOf<String?>(null,"","und","UND","und-US","zxx","English","EN-NETFLIX-StarWars","fra","fre").forEach {
            assertFalse("must not assert English for $it",preferredAudioSelected(listOf("eng"),listOf(it)))
        }
    }
    @Test fun allSelectedTracksMustMeetPreferenceAndUnknownIsNotWildcard() {
        assertFalse(preferredAudioSelected(listOf("eng"),listOf("eng","fra")))
        assertFalse(preferredAudioSelected(listOf("eng"),emptyList()))
        assertFalse(preferredAudioSelected(emptyList(),listOf("fra")))
        assertFalse(preferredAudioSelected(listOf("und"),listOf(null)))
        assertTrue(preferredAudioSelected(listOf("eng","spa"),listOf("es-MX")))
        listOf("fra" to "fre","deu" to "ger","nld" to "dut").forEach {(preferred,tag)->assertTrue(preferredAudioSelected(listOf(preferred),listOf(tag)))}
    }
    @Test fun absentRuntimeTracksKeepSuccessfulPreflightWithoutLanguageVerificationClaim() {
        assertEquals(RuntimeAudioDecision.Unobservable,runtimeAudioDecision(true,listOf("eng"),0,emptyList(),false))
        assertEquals(RuntimeAudioDecision.Blocked,runtimeAudioDecision(true,listOf("eng"),1,listOf(null),false))
        assertEquals(RuntimeAudioDecision.Blocked,runtimeAudioDecision(true,listOf("eng"),1,listOf("fra"),false))
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(true,listOf("eng"),1,listOf("en"),false))
    }
    @Test fun exactPreflightConsentAndExplicitAllDoNotCauseDuplicateRuntimePrompt() {
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(false,listOf("eng"),1,listOf("fra"),false))
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(false,emptyList(),1,listOf(null),false))
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(true,listOf("eng"),1,listOf(null),true))
    }
    @Test fun runtimeConsentCannotLeakAcrossReconnectTrackGroupIndexOrEpoch() {
        data class Group(val name:String)
        val original=Group("same value");val replacement=Group("same value")
        val approval=RuntimeAudioApproval<Group>()
        approval.approve(1,original,0)
        assertTrue(approval.accepts(1,original,0))
        assertFalse(approval.accepts(2,original,0))
        assertFalse(approval.accepts(1,replacement,0))
        assertFalse(approval.accepts(1,original,1))
        approval.clear();assertFalse(approval.accepts(1,original,0))
    }
}
