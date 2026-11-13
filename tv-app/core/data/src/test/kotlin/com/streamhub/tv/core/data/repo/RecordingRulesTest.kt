package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiErrors
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.model.RecordRequest
import com.streamhub.tv.core.data.model.Recording
import com.streamhub.tv.core.data.model.RecordingRule
import com.streamhub.tv.core.data.model.RecordingRulesResponse
import com.streamhub.tv.core.data.model.RecordingsResponse
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RecordingRulesTest {
    private fun programme(title: String, category: String? = null, isSport: Boolean = false, teams: List<String> = emptyList(), state: String? = null) =
        Programme(id = "prog:1", channelId = "ch:s:1", start = "2026-11-13T20:00:00Z", stop = "2026-11-13T21:00:00Z", title = title, category = category, recordingState = state, isSport = isSport, teams = teams)

    @Test fun aRecordingRequestCarriesTheIdAndTheChannelAndStartShownOnTheGuide() {
        assertEquals(RecordRequest("prog:1", "ch:s:1", "2026-11-13T20:00:00Z"), recordRequest(programme("Evening News")))
        assertEquals(
            """{"programme_id":"prog:1","channel_id":"ch:s:1","start":"2026-11-13T20:00:00Z"}""",
            StreamHubJson.encodeToString(RecordRequest.serializer(), recordRequest(programme("Evening News"))),
        )
    }

    @Test fun aMatchupTheServerFoundOffersOneActionPerSide() {
        val game = programme("League: Northside vs Harbour City", isSport = true, teams = listOf("Northside", "Harbour City"))
        assertEquals(TeamRecording.Teams(listOf("Northside", "Harbour City")), teamRecording(game))
        // In title order, without blanks or a side named twice.
        assertEquals(TeamRecording.Teams(listOf("Harbour City", "Northside")), teamRecording(programme("x", isSport = true, teams = listOf(" Harbour City ", "", "Northside", "Harbour City"))))
    }

    @Test fun sportWithoutNamedSidesAsksForTheTeam() {
        assertEquals(TeamRecording.AskName, teamRecording(programme("Grand Prix Highlights", isSport = true)))
        assertEquals(TeamRecording.AskName, teamRecording(programme("Evening Round-up", isSport = true, teams = listOf("  "))))
    }

    @Test fun onlyTheServersFlagMakesAListingSport() {
        // Words that sound like sport do not: the server's classifier decides, as it does for the recorder.
        assertEquals(TeamRecording.NotOffered, teamRecording(programme("Football Focus", category = "Sports")))
        assertEquals(TeamRecording.NotOffered, teamRecording(programme("Northside vs Harbour City")))
        // Teams on a listing the server does not call sport are not offered either.
        assertEquals(TeamRecording.NotOffered, teamRecording(programme("Quiz Night", teams = listOf("Reds", "Blues"))))
        // The flag alone is enough, whatever the words.
        assertEquals(TeamRecording.AskName, teamRecording(programme("Evening News", category = "News", isSport = true)))
    }

    @Test fun sidesAreShownAsAMatchup() {
        assertEquals("Northside vs Harbour City", teamsLabel(listOf("Northside", "Harbour City")))
        assertEquals("Northside", teamsLabel(listOf("Northside", " ")))
        assertNull(teamsLabel(emptyList()))
    }

    @Test fun aRecordingWithAFilePlaysUnderItsRecId() {
        fun recording(state: String, size: Long? = null) = Recording(id = "r9", title = "t", state = state, sizeBytes = size)
        assertTrue(recording("done").isPlayable())
        assertTrue(recording("done", 5_000_000).isPlayable())
        // Failed part-way: playable only when it kept something.
        assertTrue(recording("failed", 5_000_000).isPlayable())
        assertFalse(recording("failed").isPlayable())
        assertFalse(recording("failed", 0).isPlayable())
        for (state in listOf("scheduled", "recording", "missed", "cancelled", "some_future_state")) assertFalse(state, recording(state, 9).isPlayable())
        assertEquals("rec:r9", recording("done").playbackId())
    }

    @Test fun stateLabelsAndMarks() {
        assertEquals("Will record", recordingStateLabel("scheduled"))
        assertEquals("Recording", recordingStateLabel("recording"))
        assertEquals("Recorded", recordingStateLabel("done"))
        assertEquals("Recording failed", recordingStateLabel("failed"))
        assertEquals("Missed", recordingStateLabel("missed"))
        assertNull(recordingStateLabel(null))
        assertEquals("On hold", recordingStateLabel("on_hold"))
        assertEquals("○", recordingStateMark("scheduled"))
        assertEquals("●", recordingStateMark("recording"))
        assertEquals("✓", recordingStateMark("done"))
        assertEquals("!", recordingStateMark("failed"))
        assertNull(recordingStateMark(null))
        assertNull(recordingStateMark("cancelled"))
    }

    private class Fake(var fail: Exception? = null) : RecordingsRepository {
        val calls = mutableListOf<String>()
        override val availability = MutableStateFlow(RecordingAvailability(true, true))
        override suspend fun refreshAvailability() = availability.value
        override suspend fun recordings() = RecordingsOverview(availability.value, emptyList())
        override suspend fun rules() = emptyList<RecordingRule>()
        override suspend fun record(programme: Programme): Recording { fail?.let { throw it }; calls += "record:${programme.id}"; return Recording(id = "r1", title = programme.title, state = "scheduled") }
        override suspend fun addRule(kind: String, match: String, channelId: String?, newOnly: Boolean): RecordingRule {
            fail?.let { throw it }; calls += "rule:$kind:$match:$channelId:$newOnly"; return RecordingRule(id = "rule1", kind = kind, match = match, newOnly = newOnly)
        }
        override suspend fun remove(recordingId: String) { calls += "remove:$recordingId" }
        override suspend fun setRuleEnabled(ruleId: String, enabled: Boolean) = RecordingRule(id = ruleId, enabled = enabled)
        override suspend fun deleteRule(ruleId: String) { calls += "deleteRule:$ruleId" }
    }

    @Test fun recordThisSchedulesTheProgramme() = runTest {
        val fake = Fake()
        assertEquals(RecordOutcome.Done("Evening News will be recorded.", "scheduled"), fake.recordOnce(programme("Evening News")))
        assertEquals(listOf("record:prog:1"), fake.calls)
    }

    @Test fun everyNewEpisodeIsASeriesRuleOnTheTitleForNewEpisodesOnAnyChannel() = runTest {
        val fake = Fake()
        val outcome = fake.recordSeries(programme("The Long Show")) as RecordOutcome.Done
        assertEquals(listOf("rule:series:The Long Show:null:true"), fake.calls)
        assertEquals("Every new episode of The Long Show will be recorded.", outcome.message)
        assertNull(outcome.state)
    }

    @Test fun everyGameIsATeamRuleOnTheTypedName() = runTest {
        val fake = Fake()
        val outcome = fake.recordTeam("  Lakers ") as RecordOutcome.Done
        assertEquals(listOf("rule:team:Lakers:null:false"), fake.calls)
        assertEquals("Every game for Lakers will be recorded.", outcome.message)
        assertTrue(fake.recordTeam("   ") is RecordOutcome.Failed)
        assertEquals(1, fake.calls.size)
    }

    @Test fun refusalsAreWordedForTheDialog() = runTest {
        fun refusal(status: Int, code: String) = ApiException(status, ApiError(code, "server text"), "server text")
        assertEquals("Already over", (Fake(refusal(409, "programme_ended")).recordOnce(programme("x")) as RecordOutcome.Failed).error.title)
        assertEquals("Already being recorded", (Fake(refusal(409, "already_scheduled")).recordOnce(programme("x")) as RecordOutcome.Failed).error.title)
        assertEquals("The guide has changed", (Fake(refusal(409, "guide_changed")).recordOnce(programme("x")) as RecordOutcome.Failed).error.title)
        assertEquals("Too many rules", (Fake(refusal(409, "rule_limit")).recordSeries(programme("x")) as RecordOutcome.Failed).error.title)
        assertEquals("Recording isn't set up", ApiErrors.describe(refusal(409, "recording_unavailable")).title)
        assertEquals("Not allowed", ApiErrors.describe(refusal(403, "recording_forbidden")).title)
    }

    @Test fun recordingsAndRulesDecodeFromTheWire() {
        val list = StreamHubJson.decodeFromString(RecordingsResponse.serializer(), """
            {"enabled":true,"can_schedule":true,"recordings":[
              {"id":"r1","profile_id":"p1","profile_name":"Sam","rule_id":null,"channel_id":"ch:s:1","channel_name":"One","title":"Evening News",
               "subtitle":null,"season":null,"episode":null,"is_sport":false,"starts_at":"2026-11-13T20:00:00Z","ends_at":"2026-11-13T21:00:00Z",
               "pad_before_s":60,"pad_after_s":120,"state":"failed","error":"Source offline","size_bytes":null,"created_at":"x","updated_at":"y"},
              {"id":"r2","state":"some_future_state"}]}
        """.trimIndent())
        assertTrue(list.enabled)
        assertEquals("Source offline", list.recordings[0].error)
        assertFalse(list.recordings[0].isUpcoming())
        assertEquals("some_future_state", list.recordings[1].state)
        val off = StreamHubJson.decodeFromString(RecordingsResponse.serializer(), """{"enabled":false,"recordings":[]}""")
        assertFalse(off.enabled)
        assertNull(off.canSchedule)
        val rules = StreamHubJson.decodeFromString(RecordingRulesResponse.serializer(), """{"enabled":true,"rules":[{"id":"x","profile_id":"p1","kind":"team","match":"Lakers","channel_id":null,"new_only":false,"keep":null,"enabled":false,"created_at":"x"}]}""")
        assertEquals("team", rules.rules[0].kind)
        assertFalse(rules.rules[0].enabled)
        val p = StreamHubJson.decodeFromString(Programme.serializer(), """{"id":"prog:1","channel_id":"c","start":"a","stop":"b","title":"t","recording_state":"scheduled"}""")
        assertEquals("scheduled", p.recordingState)
        // A server that predates the flags: not sport, no sides.
        assertFalse(p.isSport)
        assertTrue(p.teams.isEmpty())
        val game = StreamHubJson.decodeFromString(Programme.serializer(), """{"id":"prog:2","channel_id":"c","start":"a","stop":"b","title":"League: Northside at Harbour City","is_sport":true,"teams":["Northside","Harbour City"],"recording_state":null}""")
        assertTrue(game.isSport)
        assertEquals(listOf("Northside", "Harbour City"), game.teams)
        val recorded = StreamHubJson.decodeFromString(Recording.serializer(), """{"id":"r3","title":"League: Northside at Harbour City","is_sport":true,"teams":["Northside","Harbour City"],"state":"done","size_bytes":123}""")
        assertEquals(listOf("Northside", "Harbour City"), recorded.teams)
        assertTrue(recorded.isPlayable())
        assertNull(StreamHubJson.decodeFromString(Programme.serializer(), """{"id":"prog:1","channel_id":"c","start":"a","stop":"b","title":"t","recording_state":null}""").recordingState)
    }
}

class PlayFailureWordingTest {
    private val refusal = ApiException(404, ApiError("not_found", "recording not found"), "recording not found")

    @Test fun aRecordingIsRefusedInTheServersWords() {
        assertEquals("Can't play this recording", describePlayFailure("rec:r9", refusal).title)
        assertEquals("Recording not found.", describePlayFailure("rec:r9", refusal).body)
    }

    @Test fun everythingElseKeepsTheGeneralWording() {
        assertEquals("No longer available", describePlayFailure("tmdb:movie:603", refusal).title)
        assertEquals("No longer available", describePlayFailure("ch:s1:7", refusal).title)
    }
}
