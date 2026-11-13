package com.streamhub.tv.core.data.auth

/** Legacy sessions gain a stable private identity on first guarded refresh; no storage deletion. */
fun switchSessionGeneration(session: Session): String? = session.generation ?: session.refreshToken?.takeIf {it.isNotBlank()}?.let {
    java.util.UUID.nameUUIDFromBytes(it.toByteArray(Charsets.UTF_8)).toString()
}
fun sameSwitchSession(expected: Session, current: Session): Boolean =
    expected.serverUrl==current.serverUrl && expected.profile?.id==current.profile?.id &&
        expected.refreshToken!=null && current.refreshToken!=null &&
        switchSessionGeneration(expected)==switchSessionGeneration(current)

/** A competing refresh response may not overwrite the latest rotation or another session. */
fun canApplyRefresh(expected: Session,current: Session,targetProfileId: String?): Boolean =
    sameSwitchSession(expected,current) && expected.refreshToken==current.refreshToken &&
        targetProfileId!=null && targetProfileId==expected.profile?.id

/**
 * What is stored after POST /auth/refresh answered [tokens] for the pair [expected] held. Refresh
 * tokens rotate: the answer's refresh token replaces the presented one, together with the access
 * token and the profile, in one write, and the session keeps its identity (generation) so everything
 * bound to it carries on. Null when the answer must not be stored: the session has changed hands
 * since, or a newer rotation is already in place ([current] no longer holds the token that was presented).
 */
fun refreshedSession(expected: Session, current: Session, tokens: com.streamhub.tv.core.data.model.AuthTokens): Session? =
    if (!canApplyRefresh(expected, current, tokens.profile.id)) null
    else current.copy(accessToken = tokens.accessToken, refreshToken = tokens.refreshToken, profile = tokens.profile, generation = switchSessionGeneration(expected))

/**
 * Whether a refresh asked for by a request that used [usedRefreshToken] has already been made: the
 * store holds a different (newer) refresh token. The stored pair is then used as it is. Presenting
 * the old token again would be a replay, which ends the session on the server.
 */
fun rotatedSince(usedRefreshToken: String?, stored: String?): Boolean = usedRefreshToken != null && usedRefreshToken != stored

/** The part of the session store the HTTP client's refresh needs; an interface so the refresh is tested against a fake. */
interface SessionTokens {
    suspend fun current(): Session
    /** Stores a refreshed pair atomically (see [refreshedSession]); false when it was not stored. */
    suspend fun saveRefreshedTokens(expected: Session, tokens: com.streamhub.tv.core.data.model.AuthTokens): Boolean
    /** Drops the tokens of [expected] when they are still the stored ones; the server address is kept. */
    suspend fun clearExpiredSession(expected: Session): Boolean
}
