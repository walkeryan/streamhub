package com.streamhub.tv.core.data.room

import android.view.KeyEvent
import com.streamhub.tv.core.data.auth.normalizeServerUrl

/**
 * A room code as it is typed: six characters from an alphabet without the look-alikes
 * (no I, O, 0 or 1), accepted in either case. Pure, so the rules are unit-tested.
 */
object RoomCode {
    const val ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    const val LENGTH = 6

    /** [entry] with [c] added, upper-cased; unchanged when the code is complete or [c] is not in the alphabet. */
    fun append(entry: String, c: Char): String {
        val upper = c.uppercaseChar()
        return if (entry.length >= LENGTH || upper !in ALPHABET) entry else entry + upper
    }

    fun backspace(entry: String): String = entry.dropLast(1)
    fun isComplete(entry: String): Boolean = entry.length == LENGTH && entry.all { it in ALPHABET }

    /** Whatever was pasted or typed, reduced to at most six valid characters. */
    fun normalize(raw: String): String = raw.fold("") { acc, c -> append(acc, c) }

    /** The character a remote or keyboard key types (2-9, A-Z), or null: 0, 1, I and O are not in any code. */
    fun charOf(keyCode: Int): Char? = when (keyCode) {
        in KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9 -> '0' + (keyCode - KeyEvent.KEYCODE_0)
        in KeyEvent.KEYCODE_NUMPAD_0..KeyEvent.KEYCODE_NUMPAD_9 -> '0' + (keyCode - KeyEvent.KEYCODE_NUMPAD_0)
        in KeyEvent.KEYCODE_A..KeyEvent.KEYCODE_Z -> 'A' + (keyCode - KeyEvent.KEYCODE_A)
        else -> null
    }?.takeIf { it in ALPHABET }

    /** "ABC 234": easier to read across a room than six characters in a row. */
    fun display(code: String): String = code.chunked(3).joinToString(" ")
}

/** `<server>/room/<code>`: what the QR code holds, opened by a phone or computer on the web app. */
fun roomShareUrl(serverUrl: String, code: String, joinPath: String? = null): String {
    val path = joinPath?.takeIf { it.startsWith("/") && !it.startsWith("//") } ?: "/room/$code"
    return normalizeServerUrl(serverUrl) + path
}

/**
 * The room WebSocket's address: the paired server with ws/wss for http/https, the path the join
 * response gave, and the member token (the access token never goes in a URL).
 */
fun roomSocketUrl(serverUrl: String, wsPath: String, memberToken: String): String {
    val base = normalizeServerUrl(serverUrl)
    val ws = when {
        base.startsWith("https://", ignoreCase = true) -> "wss://" + base.substring(8)
        else -> "ws://" + base.substringAfter("://")
    }
    val path = if (wsPath.startsWith("/")) wsPath else "/$wsPath"
    return ws + path + (if ('?' in path) "&" else "?") + "member_token=" + java.net.URLEncoder.encode(memberToken, "UTF-8")
}
