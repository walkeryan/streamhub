package com.streamhub.tv.core.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/* Recordings and recording rules (GET/POST/DELETE /recordings, /recording-rules). */

/** RecordingState values, as text: a state this build does not know still decodes and is shown as it is. */
object RecordingStates {
    const val SCHEDULED = "scheduled"
    const val RECORDING = "recording"
    const val DONE = "done"
    const val FAILED = "failed"
    const val MISSED = "missed"
    const val CANCELLED = "cancelled"
}

/** One programme to record: a snapshot of the guide entry taken when it was scheduled. */
@Serializable
data class Recording(
    val id: String,
    @SerialName("profile_id") val profileId: String = "",
    @SerialName("profile_name") val profileName: String? = null,
    /** The rule that scheduled it; null when scheduled by hand. */
    @SerialName("rule_id") val ruleId: String? = null,
    @SerialName("channel_id") val channelId: String = "",
    @SerialName("channel_name") val channelName: String? = null,
    val title: String = "",
    val subtitle: String? = null,
    val season: Int? = null,
    val episode: Int? = null,
    @SerialName("is_sport") val isSport: Boolean = false,
    /** As Programme.teams, from the recorded programme's title. */
    val teams: List<String> = emptyList(),
    @SerialName("starts_at") val startsAt: String = "",
    @SerialName("ends_at") val endsAt: String = "",
    val state: String = RecordingStates.SCHEDULED,
    /** Why it failed or was missed. */
    val error: String? = null,
    @SerialName("size_bytes") val sizeBytes: Long? = null,
)

@Serializable
data class RecordingRule(
    val id: String,
    @SerialName("profile_id") val profileId: String = "",
    @SerialName("profile_name") val profileName: String? = null,
    /** series = same title; team = a sports listing naming the team; keyword = title contains the text. */
    val kind: String = "",
    val match: String = "",
    /** null = any channel. */
    @SerialName("channel_id") val channelId: String? = null,
    @SerialName("channel_name") val channelName: String? = null,
    @SerialName("new_only") val newOnly: Boolean = false,
    val keep: Int? = null,
    val enabled: Boolean = true,
)

/** GET /recordings. `enabled` is false, and the list empty, until the server has a recordings directory. */
@Serializable
data class RecordingsResponse(
    val enabled: Boolean = false,
    /** The current profile may schedule, cancel and delete; absent on servers that do not say. */
    @SerialName("can_schedule") val canSchedule: Boolean? = null,
    val recordings: List<Recording> = emptyList(),
)

@Serializable
data class RecordingRulesResponse(
    val enabled: Boolean = false,
    @SerialName("can_schedule") val canSchedule: Boolean? = null,
    val rules: List<RecordingRule> = emptyList(),
)

/**
 * POST /recordings. Programme ids change whenever the guide is refreshed, so the channel and start
 * shown on the guide go with the id and the server finds the programme by those if the id is stale.
 */
@Serializable
data class RecordRequest(
    @SerialName("programme_id") val programmeId: String,
    @SerialName("channel_id") val channelId: String? = null,
    val start: String? = null,
)

@Serializable
data class RecordingRuleCreate(
    val kind: String,
    val match: String,
    @SerialName("channel_id") val channelId: String? = null,
    @SerialName("new_only") val newOnly: Boolean = false,
)

/** PATCH /recording-rules/{id}: fields left out are kept. */
@Serializable
data class RecordingRulePatch(val enabled: Boolean? = null)
