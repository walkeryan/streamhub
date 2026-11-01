package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.*

/** Restricted exit uses the dedicated owner PIN, never a target profile PIN or Google-only approval. */
enum class ProfileSwitchPath { SameProfile, OwnerPin, Secure, Legacy, Unavailable }
fun profileSwitchPath(config: AuthConfig?, current: Profile?, target: Profile): ProfileSwitchPath = when {
    config==null || current==null -> ProfileSwitchPath.Unavailable
    current.id==target.id -> ProfileSwitchPath.SameProfile
    config.contentPolicyEnabled && current.isAdmin && !current.contentRestricted && target.contentRestricted -> ProfileSwitchPath.Secure
    config.contentPolicyEnabled -> ProfileSwitchPath.OwnerPin
    config.googleRequired -> ProfileSwitchPath.Unavailable
    else -> ProfileSwitchPath.Legacy
}

/** No result is accepted after Back, session change, or a response for another target. */
suspend fun verifyOwnerPinBound(
    challenge: OwnerPinChallenge, targetId: String, pin: String,
    stillCurrent: suspend () -> Boolean,
    complete: suspend (AuthTokens) -> Unit = {},
    verify: suspend (OwnerPinVerify) -> AuthTokens,
): AuthTokens {
    require(validOwnerPin(pin))
    currentCoroutineContext().ensureActive()
    if(!stillCurrent()) throw CancellationException("Profile session changed")
    val tokens=verify(OwnerPinVerify(challenge.challengeId,challenge.pollToken,pin))
    currentCoroutineContext().ensureActive()
    if(!stillCurrent()) throw CancellationException("Profile session changed")
    require(tokens.profile.id==targetId) { "Profile switch target mismatch" }
    complete(tokens)
    return tokens
}
fun validOwnerPin(pin: String): Boolean = pin.length in 6..12 && pin.all { it in '0'..'9' }
fun ownerPinRetryDelay(seconds: Int?): Int = (seconds ?: 60).coerceIn(1,3600)

/** One submitted PIN per attempt; transient failures never replay the secret automatically. */
suspend fun awaitOwnerPin(
    challenge: OwnerPinChallenge, targetId: String,
    nextPin: suspend () -> String,
    stillCurrent: suspend () -> Boolean,
    verify: suspend (OwnerPinVerify) -> AuthTokens,
    rejected: (Int) -> Unit,
    cancel: suspend (OwnerPinCancel) -> Unit,
    authorize: suspend (suspend () -> AuthTokens) -> AuthTokens = {it()},
    complete: suspend (AuthTokens) -> Unit = {},
): AuthTokens {
    var completed=false
    try {
        val result=withTimeout(challenge.expiresIn.coerceIn(1,300)*1000L) {
            while(true) {
                try {
                    val entered=nextPin()
                    return@withTimeout authorize {verifyOwnerPinBound(challenge,targetId,entered,stillCurrent,complete=complete,verify=verify)}
                }
                catch(e:AuthorizationOutcomeUnknown) {completed=true;throw e}
                catch(e:com.streamhub.tv.core.data.api.ApiException) {
                    when {
                        e.status==429 -> {val seconds=ownerPinRetryDelay(e.retryAfterSeconds);rejected(seconds);delay(seconds*1000L)}
                        e.status==403 && e.error?.code=="owner_pin_invalid" -> rejected(0)
                        else -> throw e
                    }
                }
            }
            @Suppress("UNREACHABLE_CODE") error("unreachable")
        }
        completed=true
        return result
    } finally {
        if(!completed) withContext(NonCancellable) {withTimeoutOrNull(5000) {runCatching {if(stillCurrent())cancel(OwnerPinCancel(challenge.challengeId,challenge.pollToken))}}}
    }
}
