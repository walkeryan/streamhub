package com.streamhub.tv.feature.player

import com.streamhub.tv.core.data.model.LanguageNames
import com.streamhub.tv.core.data.model.MediaInfo
import com.streamhub.tv.core.player.PlayerStats
import java.util.Locale

/** One line of the stream information panel. */
internal data class StreamInfoLine(val label: String, val value: String)

/**
 * "Stream info" in the player menu: what the server says is playing and how it is delivered
 * (PlaybackDescriptor.media_info), then Media3's own numbers. A fact nobody knows is left out
 * rather than shown as a dash; the player's numbers are always listed.
 */
internal fun streamInfoLines(info: MediaInfo?, stats: PlayerStats, locale: Locale = Locale.getDefault()): List<StreamInfoLine> = buildList {
    if (info != null) {
        info.sourceName?.trim()?.takeIf { it.isNotEmpty() }?.let { add(StreamInfoLine("Source", it)) }
        listOfNotNull(info.container?.takeIf { it.isNotBlank() }, info.bitrateKbps?.takeIf { it > 0 }?.let(::bitrate)).joinToString(" · ")
            .takeIf { it.isNotEmpty() }?.let { add(StreamInfoLine("File", it)) }
        info.video?.let { v ->
            listOfNotNull(
                v.codec?.takeIf { it.isNotBlank() }?.uppercase(Locale.ROOT),
                resolution(v.width, v.height),
                v.frameRate?.takeIf { it > 0 }?.let { "${trim(it)} fps" },
                v.dynamicRange?.takeIf { it.isNotBlank() }?.uppercase(Locale.ROOT),
            ).joinToString(" · ").takeIf { it.isNotEmpty() }?.let { add(StreamInfoLine("Video", it)) }
        }
        info.audio?.let { a ->
            listOfNotNull(
                a.codec?.takeIf { it.isNotBlank() }?.uppercase(Locale.ROOT),
                a.channels?.let(::channels),
                LanguageNames.orNull(a.language, locale),
            ).joinToString(" · ").takeIf { it.isNotEmpty() }?.let { add(StreamInfoLine("Audio", it)) }
        }
        add(StreamInfoLine("Delivery", delivery(info)))
    }
    add(StreamInfoLine("Playing", listOfNotNull(
        resolution(stats.width, stats.height) ?: "No picture yet",
        stats.frameRate?.let { "${trim(it.toDouble())} fps" },
        listOfNotNull(stats.videoCodec, stats.audioCodec).joinToString(" + ").takeIf { it.isNotEmpty() }?.uppercase(Locale.ROOT),
    ).joinToString(" · ")))
    add(StreamInfoLine("Dropped frames", stats.droppedFrames.coerceAtLeast(0).toString()))
    add(StreamInfoLine("Buffered", "${trim(stats.bufferedMs.coerceAtLeast(0) / 1000.0)} s"))
    add(StreamInfoLine("Bandwidth estimate", if (stats.bitrateEstimateBps > 0) bitrate((stats.bitrateEstimateBps / 1000).toInt()) else "Not measured yet"))
}

private fun resolution(width: Int?, height: Int?): String? = if (width != null && height != null && width > 0 && height > 0) "$width×$height" else null

/** kbit/s below 1000, Mbit/s with one decimal above. */
private fun bitrate(kbps: Int): String = if (kbps < 1000) "$kbps kbit/s" else "${trim(kbps / 1000.0)} Mbit/s"

private fun trim(value: Double): String = String.format(Locale.ROOT, "%.1f", value).removeSuffix(".0")

private fun channels(n: Int): String = when (n) { 1 -> "Mono"; 2 -> "Stereo"; 6 -> "5.1"; 8 -> "7.1"; else -> "$n channels" }

private fun delivery(info: MediaInfo): String {
    val how = when (info.delivery) {
        "direct" -> "Direct (as the source sends it)"
        "remux" -> "Remux (repackaged, video untouched)"
        "transcode" -> "Transcode" + when (info.encoder) { "hardware" -> " (hardware encoder)"; "software" -> " (software encoder)"; else -> "" }
        else -> info.delivery.replaceFirstChar(Char::uppercase)
    }
    return listOfNotNull(how, resolution(info.outputWidth, info.outputHeight)?.let { "delivered at $it" }).joinToString(", ")
}
