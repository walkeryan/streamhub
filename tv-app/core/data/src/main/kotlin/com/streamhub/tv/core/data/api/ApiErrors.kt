package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.model.ActiveStream
import com.streamhub.tv.core.data.model.Permissions
import com.streamhub.tv.core.data.repo.SessionEnd
import kotlinx.serialization.SerializationException
import java.io.IOException
import java.nio.channels.UnresolvedAddressException

/** A failure worded for the person holding the remote. [canRetry] decides whether "Try again" is offered. */
data class UserError(val title: String, val body: String, val canRetry: Boolean)

/**
 * Turns a failed call into something a viewer can act on from the couch. Server `code`s are matched
 * first (they say what happened), then the HTTP status, then the kind of exception. The server's own
 * message is shown only where no better wording exists: it is written for logs, not for a TV screen.
 */
object ApiErrors {
    fun describe(e: Throwable): UserError = when (e) {
        is ApiException -> describeApi(e)
        // Ktor CIO reports a host that does not resolve with UnresolvedAddressException, which is not an IOException.
        is IOException, is UnresolvedAddressException -> UserError(
            "Can't reach the server",
            "Check that the StreamHub server is running and that this TV is connected to the network, then try again.",
            canRetry = true,
        )
        is SerializationException -> UserError(
            "Unexpected answer from the server",
            "The server replied with something this app version doesn't understand. Try again; if it keeps happening, update the app or the server.",
            canRetry = true,
        )
        else -> UserError("Something went wrong", e.message?.takeIf { it.isNotBlank() } ?: "Try again in a moment.", canRetry = true)
    }

    /**
     * A refused `rec:<id>` playback. A recording is refused in the server's own words (one above the
     * viewer's rating ceiling, one deleted meanwhile), and those are shown: no status code says as
     * much. Refusals that are not about the recording keep their own wording.
     */
    fun describeRecordingPlayback(e: Throwable): UserError {
        val api = e as? ApiException ?: return describe(e)
        val general = describeApi(api)
        val said = api.error?.message?.trim().orEmpty()
        if (api.error?.code in NOT_ABOUT_THE_RECORDING || api.status !in 400..499 || said.isEmpty()) return general
        val sentence = said.replaceFirstChar(Char::uppercase).let { if (it.last() in ".!?") it else "$it." }
        return UserError("Can't play this recording", sentence, canRetry = general.canRetry)
    }

    /** Refusals of playback that say nothing about the title asked for: the account, the session, the network, the streams in use. */
    private val NOT_ABOUT_THE_RECORDING = setOf(
        "user_stream_limit", "source_busy", "source_limit_reached", "remote_access_denied", "user_suspended", "session_revoked",
        "session_stale", "token_expired", "approval_pending", "forbidden", "disclaimer_required",
    )

    private fun describeApi(e: ApiException): UserError {
        val error = e.error
        val detail = error?.message?.takeIf { it.isNotBlank() }
        return when (error?.code) {
            "source_busy" -> UserError("No free stream", busyBody(error.active), canRetry = true)
            "source_limit_reached" -> UserError(
                "Stream limit reached",
                "This source is already playing as many streams as its account allows. Stop one on another device, then try again.",
                canRetry = true,
            )
            // Retrying runs the source check again, which is what offers the choice.
            "audio_selection_required" -> UserError(
                "Choose an audio source",
                "No source for this title has confirmed audio in your languages. Try again and pick the one to play.",
                canRetry = true,
            )
            "forbidden" -> UserError("Not allowed", forbiddenBody(error.missing), canRetry = false)
            "user_suspended" -> sessionEnded(SessionEnd.Suspended)
            "session_revoked" -> sessionEnded(SessionEnd.Revoked)
            "approval_pending" -> UserError(
                "Waiting for approval",
                "The owner of this StreamHub server hasn't approved this account yet. Try again once they have.",
                canRetry = true,
            )
            "user_stream_limit" -> UserError("Already watching", ownStreamsBody(error.active), canRetry = true)
            // Every call answers this away from home for a user without remote.access; so do the media URLs.
            "remote_access_denied" -> UserError(
                "Home network only",
                "This account can only be used on the home network. Connect this TV to it, or ask the owner to allow watching away from home.",
                canRetry = true,
            )
            // Reached only when the silent refresh and its one retry did not settle it (see HttpClientFactory).
            "session_stale" -> UserError(
                "Access updated",
                "What this account may do was changed on the server. Try again.",
                canRetry = true,
            )
            "room_not_found" -> UserError(
                "No room with that code",
                "Check the six characters and try again. A room closes once everyone has left it for a while.",
                canRetry = false,
            )
            "room_full" -> UserError("The room is full", "This room already has as many people as it can hold. Try again when someone has left.", canRetry = true)
            "room_limit" -> UserError("Too many rooms", "The server is already running as many watch rooms as it allows. Try again when one has closed.", canRetry = true)
            // Shown only when it is true: a stream that merely outlived a server restart is resolved again silently first (see the player).
            "playback_paused" -> UserError("Playback is stopped", "Playback was stopped on the server by an administrator. Try again once it has been resumed.", canRetry = true)
            // Reached only when fetching the room's descriptor again did not settle it (see the player).
            "stream_epoch_stale" -> UserError("Out of step with the room", "The room's stream was moved and this TV couldn't follow it. Try again, or leave and join again with the room's code.", canRetry = true)
            "room_unsupported" -> UserError("Can't be watched together", "This title can't be played in a watch room on this TV.", canRetry = false)
            // The profile picker's own routes (GET /profiles without a token, POST /auth/login) answer only at home.
            "home_network_only" -> UserError(
                "Home network only",
                "Choosing a profile with its PIN works only on the home network. Pair this TV with a code instead: that works from anywhere.",
                canRetry = false,
            )
            "pin_invalid" -> UserError("That PIN isn't right", "Check the PIN and try again.", canRetry = false)
            "current_pin_required" -> UserError("Current PIN needed", "Changing or removing a PIN needs the PIN that is set now.", canRetry = false)
            "rate_limited" -> UserError("Too many attempts", retryAfterBody(e.retryAfterSeconds), canRetry = true)
            "content_restricted" -> UserError("Not available for this profile", "This profile isn't allowed to watch this.", canRetry = false)
            "recording_forbidden" -> UserError("Not allowed", "This account can't schedule or remove recordings.", canRetry = false)
            "recording_unavailable" -> UserError("Recording isn't set up", "The server has nowhere to keep recordings, or can't record this right now.", canRetry = false)
            "programme_ended" -> UserError("Already over", "This programme has finished, so it can't be recorded.", canRetry = false)
            "already_scheduled" -> UserError("Already being recorded", "Someone else in the household has already scheduled this programme.", canRetry = false)
            "guide_changed" -> UserError("The guide has changed", "This programme is no longer where the guide showed it. Reload the guide and try again.", canRetry = false)
            "rule_limit" -> UserError("Too many rules", "This profile has as many recording rules as the server allows. Remove one under Recordings first.", canRetry = false)
            "already_available" -> UserError("Already available", "This title can already be watched here. Go back and open it again.", canRetry = false)
            "request_manager_unconfigured" -> UserError("Requests aren't set up", "The server has no request manager to pass requests to. Its owner can add one in the server's settings.", canRetry = false)
            "metadata_unavailable" -> UserError("Can't look this title up", "The server couldn't get this title's details right now, so it can't be requested. Try again in a moment.", canRetry = true)
            "disclaimer_required" -> UserError(
                "Legal notice not accepted",
                "This can't play until the legal notice has been accepted for this profile. Restart StreamHub to read and accept it.",
                canRetry = false,
            )
            else -> when (e.status) {
                401 -> sessionEnded(SessionEnd.Expired)
                403 -> UserError("Not allowed", "This profile isn't allowed to do that.", canRetry = false)
                404 -> UserError("No longer available", "The server can't find this any more. It may have been removed from its source.", canRetry = false)
                in 500..599 -> UserError(
                    "Server problem",
                    "The StreamHub server couldn't finish this (error ${e.status}). It may be restarting or its source may be down; try again in a moment.",
                    canRetry = true,
                )
                // ApiException(0) is raised before any request: there is no server address to call.
                0 -> UserError("No server set", "This TV isn't connected to a StreamHub server yet.", canRetry = false)
                else -> UserError("That didn't work", detail ?: "The server refused the request (error ${e.status}).", canRetry = e.status == 408 || e.status == 409 || e.status == 429)
            }
        }
    }

    private fun retryAfterBody(seconds: Int?): String = when {
        seconds == null || seconds <= 0 -> "Wait a little, then try again."
        seconds < 90 -> "Wait $seconds seconds, then try again."
        else -> "Wait about ${(seconds + 59) / 60} minutes, then try again."
    }

    /** Why this TV's session stopped working, for the dialog shown before the picker or the pairing screen. */
    fun sessionEnded(end: SessionEnd): UserError = when (end) {
        SessionEnd.Expired -> UserError("Sign-in expired", "This TV is no longer signed in to the server. Pair it again to continue.", canRetry = false)
        SessionEnd.Revoked -> UserError(
            "Signed out",
            "This TV was signed out on the server, or its session was handed to someone else. Pair it again to continue.",
            canRetry = false,
        )
        SessionEnd.Suspended -> UserError(
            "Account suspended",
            "This account has been suspended. Ask the owner of this StreamHub server to reinstate it.",
            canRetry = false,
        )
    }

    /** "You don't have permission for this." and, when the server names them, what is missing. */
    private fun forbiddenBody(missing: List<String>): String {
        val named = missing.filter { it.isNotBlank() }.map(Permissions::label)
        return if (named.isEmpty()) "You don't have permission for this."
        else "You don't have permission for this. Ask the owner to let you ${named.joinToString(", ")}."
    }

    /** "You're already watching <title> on another device." when the server lists the streams in the way. */
    private fun ownStreamsBody(active: List<ActiveStream>): String {
        val titles = active.mapNotNull { it.title?.takeIf(String::isNotBlank) }
        return when (titles.size) {
            0 -> "You're already watching on as many devices as your account allows. Stop playback on one of them, then try again."
            1 -> "You're already watching ${titles[0]} on another device. Stop it there, then try again."
            else -> "You're already watching ${titles.dropLast(1).joinToString(", ")} and ${titles.last()} on other devices. Stop one of them, then try again."
        }
    }

    /** "Both streams are in use. <name> is watching <title>." when the server lists who holds them. */
    private fun busyBody(active: List<ActiveStream>): String {
        val watching = active.mapNotNull { a ->
            val title = a.title?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
            "${a.profileName?.takeIf { it.isNotBlank() } ?: "Someone"} is watching $title."
        }
        if (watching.isEmpty()) {
            return "Every stream this source allows is in use. Stop playback on another device, then try again."
        }
        val lead = when (active.size) {
            1 -> "The only stream is in use."
            2 -> "Both streams are in use."
            else -> "All ${active.size} streams are in use."
        }
        return (listOf(lead) + watching).joinToString(" ")
    }
}
