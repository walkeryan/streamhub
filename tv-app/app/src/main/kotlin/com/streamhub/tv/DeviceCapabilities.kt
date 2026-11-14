package com.streamhub.tv

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.MediaCodecList
import android.view.Display
import android.view.WindowManager
import com.streamhub.tv.shell.capabilitiesJson

/** What `capabilities()` reports: read once from the platform, nothing that needs a stream to find out. */
object DeviceCapabilities {
    fun json(context: Context): String = capabilitiesJson(
        versionName = BuildConfig.VERSION_NAME,
        versionCode = BuildConfig.VERSION_CODE,
        hdr = runCatching { hdr(context) }.getOrDefault(emptyList()),
        videoCodecs = runCatching { videoCodecs() }.getOrDefault(emptyList()),
        audioPassthrough = runCatching { passthrough(context) }.getOrDefault(emptyList()),
    )

    @Suppress("DEPRECATION")
    private fun hdr(context: Context): List<String> {
        val display = (context.getSystemService(Context.WINDOW_SERVICE) as WindowManager).defaultDisplay
        return display.hdrCapabilities?.supportedHdrTypes?.toList().orEmpty().mapNotNull {
            when (it) {
                Display.HdrCapabilities.HDR_TYPE_DOLBY_VISION -> "dolby_vision"
                Display.HdrCapabilities.HDR_TYPE_HDR10 -> "hdr10"
                Display.HdrCapabilities.HDR_TYPE_HLG -> "hlg"
                Display.HdrCapabilities.HDR_TYPE_HDR10_PLUS -> "hdr10_plus"
                else -> null
            }
        }.distinct()
    }

    private val VIDEO = mapOf("video/avc" to "h264", "video/hevc" to "hevc", "video/x-vnd.on2.vp9" to "vp9", "video/av01" to "av1", "video/dolby-vision" to "dolby_vision")

    private fun videoCodecs(): List<String> = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
        .filter { !it.isEncoder }
        .flatMap { it.supportedTypes.toList() }
        .mapNotNull { VIDEO[it.lowercase()] }
        .distinct()

    private fun passthrough(context: Context): List<String> {
        val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val encodings = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            .filter { it.type == AudioDeviceInfo.TYPE_HDMI || it.type == AudioDeviceInfo.TYPE_HDMI_ARC || it.type == AudioDeviceInfo.TYPE_HDMI_EARC }
            .flatMap { it.encodings.toList() }
        return encodings.mapNotNull {
            when (it) {
                AudioFormat.ENCODING_AC3 -> "ac3"
                AudioFormat.ENCODING_E_AC3 -> "eac3"
                AudioFormat.ENCODING_DTS -> "dts"
                AudioFormat.ENCODING_DTS_HD -> "dts_hd"
                AudioFormat.ENCODING_DOLBY_TRUEHD -> "truehd"
                else -> null
            }
        }.distinct()
    }
}
