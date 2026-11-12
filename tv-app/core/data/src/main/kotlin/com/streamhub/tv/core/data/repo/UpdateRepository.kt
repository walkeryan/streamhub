package com.streamhub.tv.core.data.repo

import android.content.Context
import android.os.Build
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.normalizeServerUrl
import com.streamhub.tv.core.data.model.TvAppRelease
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CancellationException
import javax.inject.Inject
import javax.inject.Singleton

/** A newer TV build offered by the server. [apkUrl] is absolute; [sha256] is 64 lower-case hex digits. */
data class UpdateInfo(val versionCode: Int, val versionName: String, val apkUrl: String, val sha256: String)

/**
 * Asks the paired server whether it has a newer TV build (GET /app/tv). There is no update service
 * on servers that answer 404, and nothing is offered unless the build is newer than the installed
 * one and comes with a checksum the download can be verified against.
 */
@Singleton
class UpdateRepository internal constructor(
    private val fetch: suspend () -> TvAppRelease,
    private val installedVersionCode: () -> Long,
    private val serverUrl: suspend () -> String?,
) {
    @Inject constructor(api: StreamHubApi, session: SessionStore, @ApplicationContext context: Context) :
        this({ api.tvAppRelease() }, { context.installedVersionCode() }, { session.current().serverUrl })

    /** null when there is no update service (404) or nothing newer to install. Other failures are thrown. */
    suspend fun check(): UpdateInfo? {
        val release = try { fetch() }
            catch (e: CancellationException) { throw e }
            catch (e: ApiException) { if (e.status == 404) return null else throw e }
        if (release.versionCode <= installedVersionCode()) return null
        // Without a usable checksum or address the build cannot be verified, so it is not offered.
        val sha256 = normalizeSha256(release.sha256) ?: return null
        val apkUrl = resolveApkUrl(release.apkUrl, serverUrl()) ?: return null
        return UpdateInfo(release.versionCode, release.versionName.ifBlank { release.versionCode.toString() }, apkUrl, sha256)
    }
}

/** The installed build's version code (the same number as the app's BuildConfig.VERSION_CODE). */
private fun Context.installedVersionCode(): Long = runCatching {
    val info = packageManager.getPackageInfo(packageName, 0)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
}.getOrDefault(Long.MAX_VALUE) // unknown: never offer an "update" that might be a downgrade

/** 64 hex digits, lower-cased; null for anything else. */
internal fun normalizeSha256(value: String): String? =
    value.trim().lowercase().takeIf { it.length == 64 && it.all { c -> c in '0'..'9' || c in 'a'..'f' } }

/** apk_url may be absolute or a path on the paired server; any other scheme is refused. */
internal fun resolveApkUrl(apkUrl: String, serverUrl: String?): String? {
    val url = apkUrl.trim()
    return when {
        url.startsWith("http://", ignoreCase = true) || url.startsWith("https://", ignoreCase = true) -> url
        url.startsWith("/") && !url.startsWith("//") && !serverUrl.isNullOrBlank() -> normalizeServerUrl(serverUrl) + url
        else -> null
    }
}

/** Whether [url] is served by the paired server itself, the only place the session token may be sent. */
internal fun sameOrigin(url: String, serverUrl: String?): Boolean =
    !serverUrl.isNullOrBlank() && url.startsWith(normalizeServerUrl(serverUrl) + "/", ignoreCase = true)

internal fun sha256Hex(digest: ByteArray): String = digest.joinToString("") { "%02x".format(it) }
