package com.streamhub.tv.core.data.repo

import java.net.URI
import java.security.MessageDigest
import java.util.Locale

/** Credentials/query/fragment are excluded before hashing; legacy unscoped entries stay untouched. */
fun recentSearchStorageKey(serverUrl: String, profileId: String): String? {
    val uri=runCatching { URI(serverUrl) }.getOrNull() ?: return null
    val scheme=uri.scheme?.lowercase(Locale.ROOT) ?: return null
    if(scheme !in listOf("http","https") || uri.host.isNullOrBlank() || profileId.isBlank()) return null
    val port=uri.port.takeIf { it>=0 && !(scheme=="https" && it==443) && !(scheme=="http" && it==80) }?.let { ":$it" }.orEmpty()
    val path=uri.path.orEmpty().trimEnd('/').removeSuffix("/api/v1").trimEnd('/')
    val identity="$scheme://${uri.host.lowercase(Locale.ROOT)}$port$path"
    val hash=MessageDigest.getInstance("SHA-256").digest(identity.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
    return "recent_v2.$hash.$profileId"
}
