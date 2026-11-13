package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.UserError
import com.streamhub.tv.core.data.model.Permissions
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.model.RecordRequest
import com.streamhub.tv.core.data.model.Recording
import com.streamhub.tv.core.data.model.RecordingRule
import com.streamhub.tv.core.data.model.RecordingRuleCreate
import com.streamhub.tv.core.data.model.RecordingRulePatch
import com.streamhub.tv.core.data.model.RecordingStates
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/** Whether there is recording to show ([enabled]) and whether this user may schedule, cancel and delete. */
data class RecordingAvailability(val enabled: Boolean = false, val canSchedule: Boolean = false)

data class RecordingsOverview(val availability: RecordingAvailability, val recordings: List<Recording>)

/** The server's recorder as the screens use it; an interface so view models are tested against a fake. */
interface RecordingsRepository {
    /** What the server last said, for showing or hiding the Recordings destination and the Record actions. */
    val availability: StateFlow<RecordingAvailability>
    /** Asks again (start, after a switch). Never throws: a server without recordings is simply not enabled. */
    suspend fun refreshAvailability(): RecordingAvailability
    suspend fun recordings(): RecordingsOverview
    suspend fun rules(): List<RecordingRule>
    suspend fun record(programme: Programme): Recording
    /** [kind] is `series`, `team` or `keyword`. */
    suspend fun addRule(kind: String, match: String, channelId: String? = null, newOnly: Boolean = false): RecordingRule
    /** Cancels an upcoming or running recording, deletes a finished one. */
    suspend fun remove(recordingId: String)
    suspend fun setRuleEnabled(ruleId: String, enabled: Boolean): RecordingRule
    suspend fun deleteRule(ruleId: String)
}

@Singleton
class DefaultRecordingsRepository @Inject constructor(
    private val api: StreamHubApi,
    private val me: MeRepository,
) : RecordingsRepository {
    private val _availability = MutableStateFlow(RecordingAvailability())
    override val availability: StateFlow<RecordingAvailability> = _availability.asStateFlow()

    override suspend fun refreshAvailability(): RecordingAvailability {
        if (!me.can(Permissions.RECORDINGS_VIEW)) return RecordingAvailability().also { _availability.value = it }
        return try { recordings().availability }
        catch (e: CancellationException) { throw e }
        // 404 (no recorder on this server) and 403 are answers; anything else keeps what was known.
        catch (e: ApiException) { if (e.status == 404 || e.status == 403) RecordingAvailability().also { _availability.value = it } else _availability.value }
        catch (_: Exception) { _availability.value }
    }

    override suspend fun recordings(): RecordingsOverview {
        val response = api.recordings()
        val availability = RecordingAvailability(
            enabled = response.enabled,
            canSchedule = response.enabled && (response.canSchedule ?: true) && me.can(Permissions.RECORDINGS_SCHEDULE),
        )
        _availability.value = availability
        return RecordingsOverview(availability, response.recordings)
    }

    override suspend fun rules(): List<RecordingRule> = api.recordingRules().rules
    override suspend fun record(programme: Programme): Recording = api.record(recordRequest(programme))
    override suspend fun addRule(kind: String, match: String, channelId: String?, newOnly: Boolean): RecordingRule =
        api.createRecordingRule(RecordingRuleCreate(kind, match.trim(), channelId, newOnly))
    override suspend fun remove(recordingId: String) = api.deleteRecording(recordingId)
    override suspend fun setRuleEnabled(ruleId: String, enabled: Boolean): RecordingRule = api.patchRecordingRule(ruleId, RecordingRulePatch(enabled = enabled))
    override suspend fun deleteRule(ruleId: String) = api.deleteRecordingRule(ruleId)
}

@Module
@InstallIn(SingletonComponent::class)
abstract class RecordingsModule {
    @Binds abstract fun recordings(impl: DefaultRecordingsRepository): RecordingsRepository
}

// ---- Rules shared by the guide, Live TV and the Recordings screen (pure, unit-tested) ------------

/** The id, and the channel and start as the guide shows them, which still name the programme after a guide refresh. */
fun recordRequest(p: Programme): RecordRequest =
    RecordRequest(programmeId = p.id, channelId = p.channelId.takeIf(String::isNotBlank), start = p.start.takeIf(String::isNotBlank))

/**
 * Whether a guide entry is a sports listing. The contract's Programme carries no flag for it, so this
 * follows the server's own classifier (sport and league names as whole words in the category or
 * title); a server that does send `is_sport` is believed over the words.
 */
fun isSportProgramme(p: Programme): Boolean = p.isSport ?: SPORT_WORDS.containsMatchIn("${p.category.orEmpty()} ${p.title}")

private val SPORT_WORDS = Regex(
    "\\b(?:sports?|sportscenter|sportscentre|athletics|olympics|nfl|ncaa|football|basketball|nba|wnba|baseball|mlb|hockey|nhl|" +
        "cricket|ipl|soccer|epl|uefa|premier league|champions league|la ?liga|bundesliga|tennis|golf|pga|lpga|" +
        "formula 1|formula one|nascar|indycar|motogp|motorsport|rugby|boxing|ufc|mixed martial arts|wrestling)\\b",
    RegexOption.IGNORE_CASE,
)

/**
 * A first guess at the team to follow, from a listing's title: the first side of "A vs B" / "A at B"
 * / "A @ B" / "A - B", without a leading "League:" label. The viewer edits it before the rule is made.
 */
fun teamGuess(title: String): String {
    val withoutLeague = title.substringAfter(": ", title).trim().ifEmpty { title.trim() }
    val firstSide = TEAM_SEPARATOR.split(withoutLeague, limit = 2).first().trim()
    return firstSide.ifEmpty { withoutLeague }.take(TEAM_MAX_LENGTH)
}

private val TEAM_SEPARATOR = Regex("\\s+(?:vs?\\.?|versus|at|@|-|–|—)\\s+", RegexOption.IGNORE_CASE)
const val TEAM_MAX_LENGTH = 60

/** The few words shown on a guide cell and a recording card; null when there is nothing to say. */
fun recordingStateLabel(state: String?): String? = when (state) {
    null, "" -> null
    RecordingStates.SCHEDULED -> "Will record"
    RecordingStates.RECORDING -> "Recording"
    RecordingStates.DONE -> "Recorded"
    RecordingStates.FAILED -> "Recording failed"
    RecordingStates.MISSED -> "Missed"
    RecordingStates.CANCELLED -> "Cancelled"
    else -> state.replace('_', ' ').replaceFirstChar(Char::uppercase)
}

/** The mark in front of a programme title on a guide cell: a shape, not a colour. */
fun recordingStateMark(state: String?): String? = when (state) {
    RecordingStates.SCHEDULED -> "○"
    RecordingStates.RECORDING -> "●"
    RecordingStates.DONE -> "✓"
    RecordingStates.FAILED, RecordingStates.MISSED -> "!"
    else -> null
}

/** Still to come or under way: can be cancelled. */
fun Recording.isUpcoming(): Boolean = state == RecordingStates.SCHEDULED || state == RecordingStates.RECORDING

/** How a Record action ended, worded for the dialog it was pressed in. */
sealed interface RecordOutcome {
    /** [state] is the programme's recording state now, for the badge on its guide cell; null for a rule. */
    data class Done(val message: String, val state: String?) : RecordOutcome
    data class Failed(val error: UserError) : RecordOutcome
}

suspend fun RecordingsRepository.recordOnce(p: Programme): RecordOutcome = recordOutcome {
    val recording = record(p)
    RecordOutcome.Done(if (recording.state == RecordingStates.RECORDING) "Recording ${p.title} now." else "${p.title} will be recorded.", recording.state)
}

/** Rule kind `series`: every new episode of the same title, on any channel. */
suspend fun RecordingsRepository.recordSeries(p: Programme): RecordOutcome = recordOutcome {
    val rule = addRule("series", p.title, newOnly = true)
    RecordOutcome.Done("Every new episode of ${rule.match.ifBlank { p.title }} will be recorded.", null)
}

/** Rule kind `team`: every sports listing naming [team]. */
suspend fun RecordingsRepository.recordTeam(team: String): RecordOutcome {
    val wanted = team.trim()
    if (wanted.isEmpty()) return RecordOutcome.Failed(UserError("No team named", "Type the team to follow first.", canRetry = false))
    return recordOutcome {
        val rule = addRule("team", wanted)
        RecordOutcome.Done("Every game for ${rule.match.ifBlank { wanted }} will be recorded.", null)
    }
}

private suspend fun recordOutcome(block: suspend () -> RecordOutcome): RecordOutcome =
    try { block() }
    catch (e: CancellationException) { throw e }
    catch (e: Exception) { RecordOutcome.Failed(ApiErrors.describe(e)) }
