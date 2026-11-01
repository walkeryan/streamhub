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
        assertEquals(RuntimeAudioDecision.Unobservable,runtimeAudioDecision(true,listOf("eng"),1,listOf(null),false))
        assertEquals(RuntimeAudioDecision.Blocked,runtimeAudioDecision(true,listOf("eng"),1,listOf("fra"),false))
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(true,listOf("eng"),1,listOf("en"),false))
    }
    @Test fun sourceConsentDoesNotReplaceActualTrackConsentWhileAllLanguagesRemainsExplicit() {
        assertEquals(RuntimeAudioDecision.Blocked,runtimeAudioDecision(false,listOf("eng"),1,listOf("fra"),false))
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(false,emptyList(),1,listOf(null),false))
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(true,listOf("eng"),1,listOf(null),true))
    }
    @Test fun consentedSourceWaitsForRuntimeTracksAndUnavailableTagsStayUnverified() {
        assertTrue(needsRuntimeAudioCheck(false,listOf("eng")))
        assertFalse(needsRuntimeAudioCheck(false,emptyList()))
        assertEquals(RuntimeAudioDecision.Unobservable,runtimeAudioDecision(false,listOf("eng"),0,emptyList(),false))
        assertEquals(RuntimeAudioDecision.Unobservable,runtimeAudioDecision(false,listOf("eng"),1,listOf(null),false))
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(false,listOf("eng"),1,listOf("en"),false))
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(false,emptyList(),0,emptyList(),false))
    }
    @Test fun approvedForeignTrackFromConsentedSourceCannotAuthorizeChangedManifestOrMediaEpoch() {
        val original=Any();val replacement=Any();val approval=RuntimeAudioApproval<Any>()
        approval.approve(1,original,0)
        fun decision(epoch:Long,group:Any,language:String?)=runtimeAudioDecision(false,listOf("eng"),1,listOf(language),approval.accepts(epoch,group,0))
        assertEquals(RuntimeAudioDecision.Allowed,decision(1,original,"fra"))
        assertEquals(RuntimeAudioDecision.Blocked,decision(1,replacement,"fra"))
        assertEquals(RuntimeAudioDecision.Unobservable,decision(1,replacement,null))
        assertEquals(RuntimeAudioDecision.Blocked,decision(2,original,"fra"))
        approval.clear();assertEquals(RuntimeAudioDecision.Blocked,decision(1,original,"fra"))
        assertEquals(RuntimeAudioDecision.Allowed,runtimeAudioDecision(false,emptyList(),1,listOf("fra"),false))
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

    @Test fun oneUntaggedSelectedTrackPlaysAsUnknownWithoutWeakeningKnownForeignGuard() {
        listOf<String?>(null,"","und").forEach { language ->
            assertEquals(RuntimeAudioDecision.Unobservable,runtimeAudioDecision(true,listOf("eng"),1,listOf(language),false))
        }
        assertEquals(RuntimeAudioDecision.Blocked,runtimeAudioDecision(true,listOf("eng"),1,listOf("fra"),false))
        assertEquals(RuntimeAudioDecision.Blocked,runtimeAudioDecision(true,listOf("eng"),2,listOf(null),false))
        assertEquals(RuntimeAudioDecision.Blocked,runtimeAudioDecision(true,listOf("eng"),1,emptyList(),false))
        assertEquals(1,preferredAudioIndex(listOf("eng"),listOf("fra","en")))
        assertNull(preferredAudioIndex(listOf("eng"),listOf("fra",null)))
        assertFalse(preferredAudioSelected(listOf("eng"),listOf(null)))
    }
}
