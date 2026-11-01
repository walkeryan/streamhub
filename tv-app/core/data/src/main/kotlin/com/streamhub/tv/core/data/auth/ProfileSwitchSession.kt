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


/** QR challenges must return to the configured canonical Streamhub origin, never an arbitrary site. */
fun validOwnerVerificationUri(serverUrl: String?, verificationUri: String): Boolean {
    val server=runCatching { java.net.URI(serverUrl ?: return false) }.getOrNull() ?: return false
    val target=runCatching { java.net.URI(verificationUri) }.getOrNull() ?: return false
    fun port(uri: java.net.URI)=if(uri.port>=0)uri.port else if(uri.scheme=="https")443 else 80
    return target.scheme=="https" && target.userInfo==null && server.scheme==target.scheme &&
        server.host?.equals(target.host,ignoreCase=true)==true && port(server)==port(target) &&
        target.path=="/api/v1/auth/owner-reauth/verify"
}
