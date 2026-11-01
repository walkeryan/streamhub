package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.*
import kotlinx.coroutines.*

/** A PIN can never substitute for fresh owner Google authentication when leaving a restricted profile. */
enum class ProfileSwitchPath { SameProfile, OwnerGoogle, Secure, ExistingPin, Legacy, Unavailable, OwnerReauthRequired }
fun profileSwitchPath(config: AuthConfig?, current: Profile?, target: Profile): ProfileSwitchPath = when {
    config==null || current==null -> ProfileSwitchPath.Unavailable
    current.id==target.id -> ProfileSwitchPath.SameProfile
    config.contentPolicyEnabled && current.contentRestricted -> ProfileSwitchPath.OwnerGoogle
    config.contentPolicyEnabled && current.isAdmin -> ProfileSwitchPath.Secure
    config.contentPolicyEnabled && target.hasPin -> ProfileSwitchPath.ExistingPin
    config.contentPolicyEnabled -> ProfileSwitchPath.OwnerReauthRequired
    config.googleRequired -> ProfileSwitchPath.Unavailable
    else -> ProfileSwitchPath.Legacy
}

/** Bound polling: no token is accepted after cancellation, expiry or original-session change. */
suspend fun awaitOwnerReauth(
    challenge: OwnerReauthChallenge,
    targetId: String,
    stillCurrent: suspend () -> Boolean,
    poll: suspend (OwnerReauthPoll) -> AuthTokens?,
    cancel: suspend (OwnerReauthPoll) -> Unit = {},
): AuthTokens {
    val interval=challenge.interval.coerceIn(1,30)*1000L
    var completed=false
    try {
    val result = withTimeout(challenge.expiresIn.coerceIn(1,600)*1000L) {
        while(true) {
            currentCoroutineContext().ensureActive()
            if(!stillCurrent()) throw CancellationException("Profile session changed")
            val tokens=poll(OwnerReauthPoll(challenge.challengeId,challenge.pollToken))
            currentCoroutineContext().ensureActive()
            if(!stillCurrent()) throw CancellationException("Profile session changed")
            if(tokens!=null) {
                require(tokens.profile.id==targetId) { "Profile switch target mismatch" }
                return@withTimeout tokens
            }
            delay(interval)
        }
        @Suppress("UNREACHABLE_CODE") error("unreachable")
    }
    completed=true
    return result
    } finally {
        if(!completed) withContext(NonCancellable) { withTimeoutOrNull(5000) { runCatching { cancel(OwnerReauthPoll(challenge.challengeId,challenge.pollToken)) } } }
    }
}
