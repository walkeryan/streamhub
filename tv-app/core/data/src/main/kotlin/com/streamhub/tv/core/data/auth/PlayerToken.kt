package com.streamhub.tv.core.data.auth

import com.streamhub.tv.core.data.api.StreamHubJson
import java.util.Base64
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.longOrNull

/*
 * Player tokens (POST /auth/player-token). A session the web app owns gets 15-minute access tokens,
 * renewed by asking the page. Behind the full-screen player the page is hidden and does not answer,
 * so after 15 minutes progress stopped being recorded and Up next failed with "login expired". While
 * a title plays the player therefore swaps the session's token for an hour-long one and renews it
 * before it runs out, itself. The server ends a chain of them 12 hours after it started and still
 * validates the session on every request.
 */

/** A player token is renewed when it has less than this left (a 15-minute session token always is). */
const val PLAYER_TOKEN_RENEW_BEFORE_S = 20 * 60L

/**
 * Seconds until [token] expires by its own `exp` claim, read without verifying it: only to decide
 * when to renew, never to trust it. Null when it is not a JWT with an expiry.
 */
fun tokenSecondsLeft(token: String?, nowSeconds: Long): Long? {
    val body = token?.split('.')?.takeIf { it.size == 3 }?.get(1) ?: return null
    val json = runCatching { String(Base64.getUrlDecoder().decode(body.trimEnd('='))) }.getOrNull() ?: return null
    val claims = runCatching { StreamHubJson.parseToJsonElement(json) }.getOrNull() as? JsonObject ?: return null
    val exp = (claims["exp"] as? JsonPrimitive)?.longOrNull ?: return null
    return exp - nowSeconds
}

/** Whether a player should swap its session's token now. Only for a session the web app owns. */
fun playerTokenDue(session: Session, nowSeconds: Long): Boolean =
    session.webOwned && (tokenSecondsLeft(session.accessToken, nowSeconds)?.let { it < PLAYER_TOKEN_RENEW_BEFORE_S } ?: true)

/**
 * The session after a player token minted for [owner] arrived. Only a web-owned session of the same
 * person and hand-over takes it (someone switched in meanwhile keeps their own); null otherwise.
 */
fun sessionWithPlayerToken(owner: Session, current: Session, access: String): Session? {
    if (!current.webOwned || !sameSwitchSession(owner, current) || !plausibleToken(access)) return null
    return current.copy(accessToken = access)
}
