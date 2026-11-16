package com.streamhub.tv.shell

import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.repo.UpdateException
import com.streamhub.tv.core.data.repo.UpdateInfo
import com.streamhub.tv.core.data.repo.UpdateInstaller
import kotlinx.coroutines.CancellationException

/**
 * Download [info], verify it against its published SHA-256, then hand it to the system installer.
 * [status] hears the progress; the return value is the last thing to tell the viewer. Shared by the
 * TV settings screen and the prompt the shell shows when it starts.
 */
suspend fun downloadAndInstall(installer: UpdateInstaller, info: UpdateInfo, status: (String) -> Unit): String {
    status("Downloading…")
    return try {
        var shown = -1
        val apk = installer.download(info) { fraction ->
            val percent = fraction?.let { (it * 100).toInt() }
            if (percent != null && percent != shown) { shown = percent; status("Downloading… $percent%") }
        }
        installer.launchInstaller(apk)
        "Download verified. Confirm in the installer to finish. If no installer opened, allow StreamHub to install apps in this TV's settings, then choose Install again."
    } catch (e: CancellationException) { throw e }
    catch (e: UpdateException) { e.message ?: "The update could not be installed." }
    catch (_: android.content.ActivityNotFoundException) { "This TV has no installer that can open the update." }
    catch (e: Exception) { ApiErrors.describe(e).body }
}

/**
 * When the shell asks the server for a newer build by itself: once the web app is up (never over the
 * player or pairing), at most every [RECHECK_MS] while the app stays running, and never again for a
 * build the viewer answered "Later" to in this run. Pure, so it is unit-tested.
 */
object UpdatePromptRules {
    const val RECHECK_MS = 6 * 60 * 60 * 1000L

    fun dueToCheck(onWebApp: Boolean, playerOpen: Boolean, lastCheckMs: Long?, nowMs: Long): Boolean =
        onWebApp && !playerOpen && (lastCheckMs == null || nowMs - lastCheckMs >= RECHECK_MS)

    fun offer(found: UpdateInfo?, declinedVersionCode: Int?): UpdateInfo? =
        found?.takeIf { it.versionCode != declinedVersionCode }
}
