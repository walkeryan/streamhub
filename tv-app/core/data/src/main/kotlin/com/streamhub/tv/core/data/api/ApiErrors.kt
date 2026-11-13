package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.model.ActiveStream
import com.streamhub.tv.core.data.model.Permissions
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
            "user_suspended" -> UserError(
                "Account suspended",
                "This account has been suspended. Ask the owner of this StreamHub server to reinstate it.",
                canRetry = false,
            )
            "approval_pending" -> UserError(
                "Waiting for approval",
                "The owner of this StreamHub server hasn't approved this account yet. Try again once they have.",
                canRetry = true,
            )
            "user_stream_limit" -> UserError("Already watching", ownStreamsBody(error.active), canRetry = true)
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
            "room_unsupported" -> UserError("Can't be watched together", "This title can't be played in a watch room on this TV.", canRetry = false)
            "content_restricted" -> UserError("Not available for this profile", "This profile isn't allowed to watch this.", canRetry = false)
            "disclaimer_required" -> UserError(
                "Legal notice not accepted",
                "This can't play until the legal notice has been accepted for this profile. Restart StreamHub to read and accept it.",
                canRetry = false,
            )
            else -> when (e.status) {
                401 -> UserError("Sign-in expired", "This TV is no longer signed in to the server. Pair it again to continue.", canRetry = false)
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
