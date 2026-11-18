package com.streamhub.tv.core.data.model

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.nullable
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonDecoder

/*
 * The player revamp's optional descriptor fields (design doc "Shared contract"): skip markers, the
 * episode's place in its show, the episodes either side of it and scrub thumbnails. Every one of
 * them is optional and decoded leniently: an extra the server got wrong is left out, it never fails
 * the descriptor (and with it playback). A server that sends none of them plays as before.
 */

/** A stretch of the title the viewer may skip: `intro` or `credits` (anything else is ignored). */
@Serializable
data class PlaybackMarker(
    val type: String = "",
    @SerialName("start_ms") val startMs: Long = -1,
    @SerialName("end_ms") val endMs: Long = -1,
) {
    /** A known type over a real stretch of time. */
    val valid: Boolean get() = (type == INTRO || type == CREDITS) && startMs >= 0 && endMs > startMs

    companion object {
        const val INTRO = "intro"
        const val CREDITS = "credits"
    }
}

/** The show an episode belongs to. */
@Serializable
data class PlaybackShow(val id: String = "", val title: String = "")

/**
 * The episode after (or before) the one playing. [id] is a hub id `/playback/resolve` accepts;
 * [subtitle] reads like "S2 · E2".
 */
@Serializable
data class UpNext(
    val id: String = "",
    val title: String = "",
    val subtitle: String = "",
    val image: String? = null,
    @SerialName("duration_ms") val durationMs: Long? = null,
)

/** Scrub previews: `GET <url>?ms=<position>` answers the nearest frame as an image. */
@Serializable
data class PlaybackThumbnails(
    val url: String = "",
    @SerialName("interval_ms") val intervalMs: Long = 0,
)

/**
 * Decodes [inner] and answers null instead of failing, for an optional object of the descriptor.
 * Outside JSON (nothing in this app) it decodes as [inner] does.
 */
open class OrNullSerializer<T : Any>(private val inner: KSerializer<T>) : KSerializer<T?> {
    override val descriptor: SerialDescriptor = inner.descriptor.nullable

    override fun deserialize(decoder: Decoder): T? {
        val json = decoder as? JsonDecoder ?: return inner.deserialize(decoder)
        val element = json.decodeJsonElement()
        return runCatching { json.json.decodeFromJsonElement(inner, element) }.getOrNull()
    }

    override fun serialize(encoder: Encoder, value: T?) {
        if (value == null) encoder.encodeNull() else inner.serialize(encoder, value)
    }
}

object UpNextOrNull : OrNullSerializer<UpNext>(UpNext.serializer())
object PlaybackShowOrNull : OrNullSerializer<PlaybackShow>(PlaybackShow.serializer())
object PlaybackThumbnailsOrNull : OrNullSerializer<PlaybackThumbnails>(PlaybackThumbnails.serializer())

/** `markers`: every entry that decodes and is [PlaybackMarker.valid]; anything else (null, not a list) is none. */
object PlaybackMarkersSerializer : KSerializer<List<PlaybackMarker>> {
    private val list = ListSerializer(PlaybackMarker.serializer())
    override val descriptor: SerialDescriptor = list.descriptor

    override fun deserialize(decoder: Decoder): List<PlaybackMarker> {
        val json = decoder as? JsonDecoder ?: return list.deserialize(decoder).filter { it.valid }
        val array = json.decodeJsonElement() as? JsonArray ?: return emptyList()
        return array.mapNotNull { e -> runCatching { json.json.decodeFromJsonElement(PlaybackMarker.serializer(), e) }.getOrNull()?.takeIf { it.valid } }
    }

    override fun serialize(encoder: Encoder, value: List<PlaybackMarker>) = list.serialize(encoder, value)
}
