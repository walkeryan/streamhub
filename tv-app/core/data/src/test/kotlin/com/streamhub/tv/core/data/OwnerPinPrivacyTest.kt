package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.OwnerPinCancel
import com.streamhub.tv.core.data.model.OwnerPinChallenge
import com.streamhub.tv.core.data.model.OwnerPinVerify
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class OwnerPinPrivacyTest {
    private val challenge="fixture-challenge-private"
    private val poll="fixture-poll-private"
    private val pin="123456"
    @Test fun diagnosticsRedactEveryCredentialIncludingCopies() {
        val verify=OwnerPinVerify(challenge,poll,pin)
        val values=listOf(verify,verify.copy(pin="654321"),OwnerPinChallenge(challenge,poll,300),OwnerPinCancel(challenge,poll))
        values.forEach {
            val diagnostic=it.toString()
            listOf(challenge,poll,pin,"654321").forEach { secret->assertFalse(diagnostic.contains(secret)) }
            assertTrue(diagnostic.contains("[REDACTED]"))
        }
    }
    @Test fun apiSerializationKeepsExactFieldsWithoutUsingDiagnostics() {
        val verify=StreamHubJson.parseToJsonElement(StreamHubJson.encodeToString(OwnerPinVerify(challenge,poll,pin))).jsonObject
        assertEquals(challenge,verify["challenge_id"]?.jsonPrimitive?.content)
        assertEquals(poll,verify["poll_token"]?.jsonPrimitive?.content)
        assertEquals(pin,verify["pin"]?.jsonPrimitive?.content)
        val cancel=StreamHubJson.parseToJsonElement(StreamHubJson.encodeToString(OwnerPinCancel(challenge,poll))).jsonObject
        assertEquals(poll,cancel["poll_token"]?.jsonPrimitive?.content)
        val start=StreamHubJson.decodeFromString<OwnerPinChallenge>("""{"challenge_id":"fixture-challenge-private","poll_token":"fixture-poll-private","expires_in":300}""")
        assertEquals(challenge,start.challengeId);assertEquals(poll,start.pollToken);assertEquals(300,start.expiresIn)
    }
}
