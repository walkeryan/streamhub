package com.streamhub.tv.core.data.repo

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import com.streamhub.tv.core.data.auth.SessionStore
import dagger.hilt.android.qualifiers.ApplicationContext
import io.ktor.client.HttpClient
import io.ktor.client.plugins.auth.AuthCircuitBreaker
import io.ktor.client.plugins.timeout
import io.ktor.client.request.prepareGet
import io.ktor.client.statement.bodyAsChannel
import io.ktor.http.contentLength
import io.ktor.http.isSuccess
import io.ktor.utils.io.jvm.javaio.toInputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.File
import java.security.MessageDigest
import javax.inject.Inject
import javax.inject.Singleton

/** A failed update, worded for the About screen. */
class UpdateException(message: String) : Exception(message)

/**
 * Downloads an offered build into app-private storage, refuses it unless its SHA-256 matches the
 * one the server published, and hands the verified file to the system package installer through a
 * FileProvider (the installer cannot read this app's private files directly).
 */
@Singleton
class UpdateInstaller @Inject constructor(
    @ApplicationContext private val context: Context,
    private val client: HttpClient,
    private val session: SessionStore,
) {
    /** @param onProgress fraction downloaded, or null while the size is unknown; called off the main thread */
    suspend fun download(info: UpdateInfo, onProgress: (Float?) -> Unit = {}): File = withContext(Dispatchers.IO) {
        val dir = File(context.cacheDir, DIRECTORY).apply { mkdirs() }
        // One update at a time: an older or half-written file is never reused.
        dir.listFiles()?.forEach { it.delete() }
        val target = File(dir, "streamhub-tv-${info.versionCode}.apk")
        val part = File(dir, target.name + ".part")
        val digest = MessageDigest.getInstance("SHA-256")
        val fromPairedServer = sameOrigin(info.apkUrl, session.current().serverUrl)
        try {
            client.prepareGet(info.apkUrl) {
                // The session token goes to the paired server only, never to wherever apk_url points.
                if (!fromPairedServer) attributes.put(AuthCircuitBreaker, Unit)
                timeout { requestTimeoutMillis = DOWNLOAD_TIMEOUT_MS; socketTimeoutMillis = SOCKET_TIMEOUT_MS }
            }.execute { response ->
                if (!response.status.isSuccess()) throw UpdateException("The update could not be downloaded (error ${response.status.value}).")
                val total = response.contentLength()
                if (total != null && total > MAX_APK_BYTES) throw UpdateException("The update file is larger than this app accepts.")
                var read = 0L
                response.bodyAsChannel().toInputStream().use { input ->
                    part.outputStream().use { out ->
                        val buffer = ByteArray(64 * 1024)
                        while (true) {
                            val n = input.read(buffer)
                            if (n < 0) break
                            out.write(buffer, 0, n)
                            digest.update(buffer, 0, n)
                            read += n
                            if (read > MAX_APK_BYTES) throw UpdateException("The update file is larger than this app accepts.")
                            onProgress(total?.takeIf { it > 0 }?.let { (read.toFloat() / it).coerceIn(0f, 1f) })
                            currentCoroutineContext().ensureActive()
                        }
                    }
                }
            }
            if (sha256Hex(digest.digest()) != info.sha256) {
                throw UpdateException("The downloaded file does not match the checksum the server published, so it was not installed.")
            }
            if (!part.renameTo(target)) throw UpdateException("The update could not be saved on this TV.")
            target
        } catch (e: Throwable) {
            part.delete()
            throw e
        }
    }

    /** Opens the system installer on a file returned by [download]. The viewer confirms there. */
    fun launchInstaller(apk: File) {
        val uri = FileProvider.getUriForFile(context, context.packageName + AUTHORITY_SUFFIX, apk)
        context.startActivity(
            Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, APK_MIME)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }

    private companion object {
        /** Under cacheDir; must match res/xml/update_paths.xml in the app module. */
        const val DIRECTORY = "updates"
        /** Appended to the application id; must match the provider's authorities in the manifest. */
        const val AUTHORITY_SUFFIX = ".updates"
        const val APK_MIME = "application/vnd.android.package-archive"
        const val MAX_APK_BYTES = 300L * 1024 * 1024
        const val DOWNLOAD_TIMEOUT_MS = 15L * 60_000
        const val SOCKET_TIMEOUT_MS = 60_000L
    }
}
