package com.streamhub.tv.shell

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.auth.normalizeServerUrl
import com.streamhub.tv.core.data.model.PlaybackRequest
import com.streamhub.tv.core.data.model.Profile
import com.streamhub.tv.core.data.room.RoomCode
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import java.net.URI
import java.time.OffsetDateTime

/**
 * The bridge between the shell and the web app it shows, as pure functions so every rule is
 * unit-tested: what `play()` accepts, the session written into the web app, which addresses may load,
 * what Back does, and the scripts the shell runs in the page.
 *
 * Surface (version [BRIDGE_VERSION]), page → TV, on `window.StreamHubTV`:
 *  - `play(json): boolean` — start native playback ([parseShellPlay]); false when refused.
 *  - `signedOut(reason)` — the web app has no session any more; the shell returns to pairing.
 *  - `capabilities(): string` — JSON ([capabilitiesJson]).
 *  - `deliverToken(id, json)` — the answer to a token request (see WebTokenBroker).
 * TV → page, on `window.streamhubShell`: `back(): boolean`, `token(id, rejected)`, `playbackEnded()`.
 */
const val BRIDGE_VERSION = 1

/** The web app's `localStorage` key for its session (`web-app/src/store/session.ts`). */
const val WEB_SESSION_KEY = "sh.session.v1"

/** What the web app asked the TV to play. Only inputs to a resolve: never a stream address. */
sealed interface ShellPlay {
    data class Title(val request: PlaybackRequest, val startPositionMs: Long?) : ShellPlay
    /** A seat the web app took in a watch room, handed over by its member token. */
    data class Room(val code: String, val memberToken: String, val allowUnverifiedAudio: Boolean) : ShellPlay {
        override fun toString(): String = "Room(code=$code, memberToken=[REDACTED], allowUnverifiedAudio=$allowUnverifiedAudio)"
    }
}

sealed interface ParsedPlay {
    data class Ok(val play: ShellPlay) : ParsedPlay
    data class Rejected(val reason: String) : ParsedPlay
}

private const val MAX_PLAY_JSON = 16 * 1024
/** A week: longer than anything that can be played from a position. */
private const val MAX_START_MS = 7L * 24 * 3600 * 1000

/** An id as the API hands them out (`tmdb:movie:603`, `ch:…`, `rec:…`): visible ASCII, no spaces. */
internal fun validId(value: String): Boolean = value.length in 1..512 && value.all { it in '!'..'~' }

private class Invalid(message: String) : Exception(message)

private fun JsonObject.optString(key: String): String? = when (val v = this[key]) {
    null, JsonNull -> null
    is JsonPrimitive -> if (v.isString) v.content else throw Invalid("$key is not a string")
    else -> throw Invalid("$key is not a string")
}

private fun JsonObject.optId(key: String): String? = optString(key)?.also { if (!validId(it)) throw Invalid("$key is not an id") }

private fun JsonObject.optBoolean(key: String): Boolean? = when (val v = this[key]) {
    null, JsonNull -> null
    is JsonPrimitive -> if (!v.isString) v.booleanOrNull ?: throw Invalid("$key is not a boolean") else throw Invalid("$key is not a boolean")
    else -> throw Invalid("$key is not a boolean")
}

private fun JsonObject.optLong(key: String): Long? = when (val v = this[key]) {
    null, JsonNull -> null
    is JsonPrimitive -> if (!v.isString) v.longOrNull ?: throw Invalid("$key is not an integer") else throw Invalid("$key is not an integer")
    else -> throw Invalid("$key is not an integer")
}

/**
 * Reads `play(json)`. Everything is checked: the version, the kind, every field's type and shape.
 * Unknown fields are ignored (a newer web app may send more); a known field of the wrong type is a
 * refusal. The TV resolves what it is given through the API itself, with `client=android_tv`.
 */
fun parseShellPlay(json: String): ParsedPlay {
    if (json.length > MAX_PLAY_JSON) return ParsedPlay.Rejected("too long")
    val root = runCatching { StreamHubJson.parseToJsonElement(json) }.getOrNull() as? JsonObject
        ?: return ParsedPlay.Rejected("not a JSON object")
    return try {
        if (root.optLong("v") != BRIDGE_VERSION.toLong()) throw Invalid("unsupported version")
        when (root.optString("kind")) {
            "title" -> {
                val id = root.optId("id") ?: throw Invalid("id is missing")
                val catchup = root.optId("catchup_programme_id")
                val rewindAt = root.optString("rewind_at")?.also {
                    if (it.length > 64 || runCatching { OffsetDateTime.parse(it) }.isFailure) throw Invalid("rewind_at is not a date-time")
                }
                if (catchup != null && rewindAt != null) throw Invalid("catch-up and rewind together")
                val start = root.optLong("start_position_ms")?.also { if (it !in 0..MAX_START_MS) throw Invalid("start_position_ms out of range") }
                val variant = root.optId("variant_id")
                ParsedPlay.Ok(ShellPlay.Title(
                    PlaybackRequest(
                        id = id,
                        sourceId = root.optId("source_id"),
                        catchupProgrammeId = catchup,
                        variantId = variant,
                        // Consent belongs to one chosen variant; without one there is nothing it applies to.
                        allowUnverifiedAudio = variant != null && root.optBoolean("allow_unverified_audio") == true,
                        rewindAt = rewindAt,
                    ),
                    start,
                ))
            }
            "room" -> {
                val code = root.optString("room_code")?.uppercase()?.takeIf(RoomCode::isComplete) ?: throw Invalid("room_code is not a room code")
                val token = root.optString("member_token")?.takeIf { it.length in 1..2048 && it.all { c -> c in '!'..'~' } }
                    ?: throw Invalid("member_token is missing")
                ParsedPlay.Ok(ShellPlay.Room(code, token, root.optBoolean("allow_unverified_audio") == true))
            }
            else -> throw Invalid("unknown kind")
        }
    } catch (e: Invalid) {
        ParsedPlay.Rejected(e.message ?: "invalid")
    }
}

/** The value the web app keeps under [WEB_SESSION_KEY]: `{access, refresh, profile}`, as `session.ts` reads it. */
fun webSessionJson(access: String, refresh: String, profile: Profile): String = buildJsonObject {
    put("access", access)
    put("refresh", refresh)
    put("profile", StreamHubJson.encodeToJsonElement(Profile.serializer(), profile))
}.toString()

/** A JavaScript string literal for [value] (a JSON string is one). */
internal fun jsString(value: String): String = JsonPrimitive(value).toString()

/**
 * Run in a document of the server's origin before the web app loads: writes the session and says
 * whether it is there. The page loads the web app only after this answered `true`.
 */
fun seedScript(sessionJson: String): String =
    "(function(){try{localStorage.setItem(${jsString(WEB_SESSION_KEY)},${jsString(sessionJson)});" +
        "return localStorage.getItem(${jsString(WEB_SESSION_KEY)})===${jsString(sessionJson)}}catch(e){return false}})()"

/** Asks the page for a token; its answer comes back through `deliverToken(id, json)`. True when the page has the function. */
fun tokenRequestScript(id: String, rejected: String?): String =
    "(function(){var s=window.streamhubShell;if(!s||typeof s.token!=='function')return false;" +
        "s.token(${jsString(id)},${rejected?.let(::jsString) ?: "null"});return true})()"

const val BACK_SCRIPT = "(function(){var s=window.streamhubShell;return s&&typeof s.back==='function'?s.back()===true:null})()"
const val PLAYBACK_ENDED_SCRIPT = "(function(){var s=window.streamhubShell;if(s&&typeof s.playbackEnded==='function')s.playbackEnded()})()"

/** What a press of Back does: nothing more (the web app handled it), or leave the app like any TV app. */
enum class BackOutcome { Consumed, Exit }

/**
 * The web app's answer to `back()` as evaluateJavascript reports it: `"true"` when it closed a
 * dialog or went back, `"false"` on its home screen with nothing to close. Anything else (the page
 * did not load, the function is missing) leaves the app too: there is nothing in the page to go back in.
 */
fun backOutcome(answer: String?): BackOutcome = if (answer?.trim() == "true") BackOutcome.Consumed else BackOutcome.Exit

/** `scheme://host:port` of [url], lower-cased, the port made explicit; null when it is not http(s). */
internal fun originOf(url: String): String? {
    val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return null
    val scheme = uri.scheme?.lowercase()?.takeIf { it == "http" || it == "https" } ?: return null
    val host = uri.host?.lowercase()?.takeIf { it.isNotBlank() } ?: return null
    if (uri.rawUserInfo != null) return null
    val port = if (uri.port != -1) uri.port else if (scheme == "https") 443 else 80
    return "$scheme://$host:$port"
}

/** The web app's address on the paired server: its root, which the server serves the web app from. */
fun webAppUrl(serverUrl: String): String = normalizeServerUrl(serverUrl) + "/"

/**
 * Whether the shell may load [url]: only the paired server's own origin. Every other address,
 * every other scheme (`intent:`, `file:`, `javascript:`, `data:`) and links to other sites load
 * nowhere: there is no browser on the TV to send them to.
 */
fun mayLoad(serverUrl: String, url: String): Boolean {
    val server = originOf(normalizeServerUrl(serverUrl)) ?: return false
    return originOf(url) == server
}

/** `capabilities()`: what this shell is and can do, for the web app to adapt to. */
fun capabilitiesJson(versionName: String, versionCode: Int, hdr: List<String>, videoCodecs: List<String>, audioPassthrough: List<String>): String = buildJsonObject {
    put("bridge", BRIDGE_VERSION)
    put("shell", versionName)
    put("version_code", versionCode)
    put("native_playback", true)
    put("hdr", buildJsonArray { hdr.forEach { add(JsonPrimitive(it)) } })
    put("video_codecs", buildJsonArray { videoCodecs.forEach { add(JsonPrimitive(it)) } })
    put("audio_passthrough", buildJsonArray { audioPassthrough.forEach { add(JsonPrimitive(it)) } })
}.toString()
