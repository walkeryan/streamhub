package com.streamhub.tv.shell

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.feature.player.MiniPlayerInfo
import kotlinx.serialization.json.*
import java.util.UUID

/** Commands contain catalog identifiers only. The TV selects and authorizes its own stream. */
data class CastAction(val request: String, val action: String, val id: String?, val position: Long?, val play: ShellPlay?)

fun parseCastAction(payload: String?): CastAction? = runCatching {
    require(payload != null && payload.length <= 8192)
    val body = StreamHubJson.parseToJsonElement(payload).jsonObject
    val request = UUID.fromString(body.getValue("request_id").jsonPrimitive.content).toString()
    val action = body.getValue("action").jsonPrimitive.content
    require(action in setOf("load", "stop", "play", "pause", "seek"))
    val id = body["id"]?.jsonPrimitive?.contentOrNull
    val position = body["position_ms"]?.jsonPrimitive?.longOrNull
    require(action != "seek" || position != null && position in 0..604800000L)
    val play = if (action == "load") {
        val load = buildJsonObject {
            put("v", if (body["buffer_at"] != null && body["buffer_at"] != JsonNull) 2 else 1)
            put("kind", "title")
            for (key in listOf("id", "variant_id", "source_id", "audio_track", "catchup_programme_id", "rewind_at", "buffer_at")) {
                body[key]?.takeUnless { it == JsonNull }?.let { put(key, it) }
            }
            if (position != null) put("start_position_ms", position)
        }
        (parseShellPlay(load.toString()) as ParsedPlay.Ok).play
    } else null
    CastAction(request, action, id, position, play)
}.getOrNull()

object CastReceiver {
    @Volatile var interactionAt: Long = 0
    @Volatile var info: MiniPlayerInfo? = null
    var requestedTitle: String? = null
        private set

    fun attach(title: String?) {
        info = null
        requestedTitle = title
    }

    fun playingRequestedTitle(action: CastAction): Boolean =
        action.id != null && action.id == requestedTitle && info?.playing == true

    @Volatile private var last: Pair<String, String>? = null
    private val results = linkedMapOf<String, String>()
    fun begin(action: CastAction): Boolean {
        val previous = results[action.request]
        if (previous != null) { last = action.request to previous; return false }
        result(action, "pending")
        return true
    }
    fun result(action: CastAction, state: String) {
        results[action.request] = state
        if (results.size > 64) results.remove(results.keys.first())
        last = action.request to state
    }
    fun snapshot(): String {
        val state = StreamHubJson.parseToJsonElement(miniPlayerEventJson(info)).jsonObject
        val outcome = last
        return buildJsonObject {
            put("interaction_at", interactionAt)
            state.forEach { (k, v) -> put(k, v) }
            if (outcome != null) { put("request_id", outcome.first); put("result", outcome.second) }
        }.toString()
    }
}
