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
