package com.streamhub.tv.core.data.api

import com.streamhub.tv.core.data.model.ActiveStream
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
