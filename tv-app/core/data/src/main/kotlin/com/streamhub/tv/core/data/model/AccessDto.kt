package com.streamhub.tv.core.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/*
 * Users, roles and permissions (GET /auth/me, /auth/switchable, POST /auth/switch). Every field is
 * defaulted: a server that leaves one out, or sends null for it, still decodes.
 */

/** Permission names the TV asks about. The server's list is longer; unknown names are simply never held. */
object Permissions {
    const val ROOMS_JOIN = "rooms.join"
    const val ROOMS_CREATE = "rooms.create"
    const val RECORDINGS_VIEW = "recordings.view"
    const val RECORDINGS_SCHEDULE = "recordings.schedule"
    const val REQUESTS_CREATE = "requests.create"
    const val REQUESTS_APPROVE = "requests.approve"
    const val USERS_INVITE = "users.invite"
    const val USERS_MANAGE = "users.manage"
    const val SOURCES_MANAGE = "sources.manage"
    const val SYSTEM_MANAGE = "system.manage"

    /** What only an administrator could do before permissions existed; see MeState.Absent. */
    val ADMIN_ONLY: Set<String> = setOf(REQUESTS_APPROVE, USERS_INVITE, USERS_MANAGE, SOURCES_MANAGE, SYSTEM_MANAGE)

    /** A permission worded for a sentence: "You'd need permission to <label>". Unknown names read as themselves. */
    fun label(permission: String): String = when (permission) {
        "watch.library" -> "watch the library"
        "watch.vod" -> "watch on-demand titles"
        "watch.live" -> "watch live TV"
        "watch.adult" -> "open the adult category"
        ROOMS_JOIN -> "join watch rooms"
        ROOMS_CREATE -> "start watch rooms"
        RECORDINGS_VIEW -> "see recordings"
        RECORDINGS_SCHEDULE -> "schedule recordings"
        REQUESTS_CREATE -> "request titles"
        "requests.auto_approve" -> "have requests approved automatically"
        REQUESTS_APPROVE -> "approve requests"
        "remote.access" -> "use StreamHub away from home"
        USERS_INVITE -> "invite people"
        USERS_MANAGE -> "manage people"
        SOURCES_MANAGE -> "manage sources"
        SYSTEM_MANAGE -> "manage the server"
        else -> permission
    }
}

@Serializable
data class MeUser(
    val id: String = "",
    @SerialName("display_name") val displayName: String = "",
    val avatar: String? = null,
    val picture: String? = null,
    @SerialName("role_name") val roleName: String? = null,
    @SerialName("has_pin") val hasPin: Boolean = false,
)

@Serializable
data class RoleRef(val id: String = "", val name: String = "", val builtin: Boolean = false)

/** GET /auth/me: who this TV is signed in as and what they may do. */
@Serializable
data class Me(
    /** pending | active | suspended */
    val status: String = "active",
    val user: MeUser = MeUser(),
    val role: RoleRef? = null,
    val permissions: List<String> = emptyList(),
    val limits: JsonObject? = null,
    /** A household device: the "Who's watching?" picker is offered and shown on every cold start. */
    @SerialName("shared_device") val sharedDevice: Boolean = false,
    @SerialName("adult_unlocked") val adultUnlocked: Boolean = false,
    /** Set once, when a user who was waiting for approval has been approved. */
    val tokens: AuthTokens? = null,
) {
    override fun toString(): String = "Me(status=$status, user=${user.id}, role=${role?.name}, permissions=$permissions, sharedDevice=$sharedDevice, tokens=${if (tokens == null) "null" else "[REDACTED]"})"
}
