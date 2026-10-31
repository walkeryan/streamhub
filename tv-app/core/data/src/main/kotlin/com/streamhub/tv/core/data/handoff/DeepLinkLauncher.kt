package com.streamhub.tv.core.data.handoff

import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.util.Log
import com.streamhub.tv.core.data.model.Handoff
import com.streamhub.tv.core.data.model.ServiceRegistryEntry
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

sealed interface HandoffResult {
    /** ACTION_VIEW with the title URI was accepted by the target app. */
    data class OpenedTitle(val pkg: String) : HandoffResult

    /** Title link failed/unsupported; app home opened via its leanback launch intent. */
    data class OpenedApp(val pkg: String) : HandoffResult

    /** App not installed (or nothing launchable): caller shows a QR code of [webUrl]. */
    data class ShowQr(val webUrl: String, val serviceName: String?) : HandoffResult
}

/**
 * Hand-off launcher implementing docs/DEEPLINKS.md launch order:
 *   1. Intent(ACTION_VIEW, uri).setPackage(pkg) (+extras, +explicit component when the registry has one)
 *   2. packageManager.getLeanbackLaunchIntentForPackage(pkg) (then getLaunchIntentForPackage)
 *   3. QR of web_url (returned as [HandoffResult.ShowQr]; UI renders it)
 *
 * Package visibility: every package listed in DEEPLINKS.md is declared in the app manifest
 * <queries> block, otherwise resolveActivity/getLeanbackLaunchIntentForPackage return null on API 30+.
 */
@Singleton
class DeepLinkLauncher @Inject constructor(@ApplicationContext private val context: Context) {

    fun launch(h: Handoff, serviceName: String? = null): HandoffResult =
        launch(
            pkg = h.androidPackage,
            uri = h.androidUri,
            extras = h.androidExtras,
            component = h.androidComponent,
            webUrl = h.webUrl,
            serviceName = serviceName ?: h.serviceKey,
        )

    /** "Test link" action for a registry row (Sources → Streaming services). */
    fun test(entry: ServiceRegistryEntry): HandoffResult {
        val sampleUri = entry.androidTitleUri?.takeIf { it.isNotBlank() && it != "—" }
            ?.substringBefore(" (")
            ?.takeIf { !it.contains('{') } // templates need a real id; test the app launch instead
        return launch(
            pkg = entry.androidPackage.orEmpty(),
            uri = sampleUri,
            extras = emptyMap(),
            component = entry.androidComponent,
            webUrl = entry.webUrl.replace(Regex("\\{[^}]+\\}"), "").trimEnd('/'),
            serviceName = entry.name,
        )
    }

    fun isInstalled(pkg: String): Boolean = try {
        if (Build.VERSION.SDK_INT >= 33) {
            context.packageManager.getPackageInfo(pkg, PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            context.packageManager.getPackageInfo(pkg, 0)
        }
        true
    } catch (_: PackageManager.NameNotFoundException) {
        false
    }

    private fun launch(
        pkg: String,
        uri: String?,
        extras: Map<String, String>,
        component: String?,
        webUrl: String,
        serviceName: String?,
    ): HandoffResult {
        if (pkg.isNotBlank() && isInstalled(pkg)) {
            // 1. Title deep link
            if (!uri.isNullOrBlank()) {
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(uri)).apply {
                    setPackage(pkg)
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    extras.forEach { (k, v) -> putExtra(k, v) }
                    component?.let { c ->
                        val cls = if (c.startsWith(".")) pkg + c else c
                        setComponent(ComponentName(pkg, cls))
                    }
                }
                if (start(intent)) return HandoffResult.OpenedTitle(pkg)
                // Explicit component may be stale; retry once without it.
                if (component != null && start(intent.setComponent(null))) return HandoffResult.OpenedTitle(pkg)
            }
            // 2. Leanback launcher intent (TV home of the app)
            val launch = context.packageManager.getLeanbackLaunchIntentForPackage(pkg)
                ?: context.packageManager.getLaunchIntentForPackage(pkg)
            if (launch != null && start(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))) {
                return HandoffResult.OpenedApp(pkg)
            }
        }
        // 3. QR fallback
        return HandoffResult.ShowQr(webUrl, serviceName)
    }

    private fun start(intent: Intent): Boolean = try {
        context.startActivity(intent)
        true
    } catch (e: ActivityNotFoundException) {
        Log.i(TAG, "No activity for ${intent.`package`}: ${e.message}")
        false
    } catch (e: SecurityException) {
        Log.w(TAG, "Not allowed to launch ${intent.`package`}", e)
        false
    }

    private companion object {
        const val TAG = "DeepLinkLauncher"
    }
}
