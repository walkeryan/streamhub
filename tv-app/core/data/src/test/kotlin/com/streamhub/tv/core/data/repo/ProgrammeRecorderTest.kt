package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.model.Recording
import com.streamhub.tv.core.data.model.RecordingRule
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ProgrammeRecorderTest {
    private val programme = Programme(id = "prog:1", channelId = "ch:s:1", start = "2026-11-13T20:00:00Z", stop = "2026-11-13T21:00:00Z", title = "Evening News")

    private class Fake : RecordingsRepository {
        var gate: CompletableDeferred<Unit>? = null
        var fail: Exception? = null
        var records = 0
        var rules = mutableListOf<String>()
        override val availability = MutableStateFlow(RecordingAvailability(enabled = true, canSchedule = true))
        override suspend fun refreshAvailability() = availability.value
        override suspend fun recordings() = RecordingsOverview(availability.value, emptyList())
        override suspend fun rules() = emptyList<RecordingRule>()
        override suspend fun record(programme: Programme): Recording {
            gate?.await()
            fail?.let { throw it }
            records++
            return Recording(id = "r1", title = programme.title, state = "recording")
        }
        override suspend fun addRule(kind: String, match: String, channelId: String?, newOnly: Boolean): RecordingRule {
            rules += "$kind:$match"
            return RecordingRule(id = "rule", kind = kind, match = match)
        }
        override suspend fun remove(recordingId: String) = Unit
        override suspend fun setRuleEnabled(ruleId: String, enabled: Boolean) = RecordingRule(id = ruleId, enabled = enabled)
        override suspend fun deleteRule(ruleId: String) = Unit
    }

    @Test fun aScheduledProgrammeReportsItsNewStateForTheBadge() = runTest {
        val fake = Fake()
        val marked = mutableListOf<Pair<String, String?>>()
        val recorder = ProgrammeRecorder(fake, backgroundScope) { p, state -> marked += p.id to state }
        assertTrue(recorder.availability.value.canSchedule)
        recorder.recordOnce(programme)
        runCurrent()
        assertEquals(RecordOutcome.Done("Recording Evening News now.", "recording"), recorder.state.value.outcome)
        assertFalse(recorder.state.value.busy)
        assertEquals(listOf("prog:1" to "recording"), marked)
    }

    @Test fun aSecondPressWhileOneIsRunningIsIgnored() = runTest {
        val fake = Fake().apply { gate = CompletableDeferred() }
        val recorder = ProgrammeRecorder(fake, backgroundScope)
        recorder.recordOnce(programme)
        runCurrent()
        assertTrue(recorder.state.value.busy)
        recorder.recordOnce(programme)
        recorder.recordSeries(programme)
        // Closing the dialog mid-flight does not pretend the action never happened.
        recorder.reset()
        assertTrue(recorder.state.value.busy)
        fake.gate!!.complete(Unit)
        runCurrent()
        assertEquals(1, fake.records)
        assertTrue(fake.rules.isEmpty())
        assertFalse(recorder.state.value.busy)
    }

    @Test fun rulesDoNotMarkAProgrammeAndAFailureIsKeptForTheDialog() = runTest {
        val fake = Fake()
        val marked = mutableListOf<String>()
        val recorder = ProgrammeRecorder(fake, backgroundScope) { p, _ -> marked += p.id }
        recorder.recordSeries(programme)
        runCurrent()
        recorder.recordTeam("Lakers")
        runCurrent()
        assertEquals(listOf("series:Evening News", "team:Lakers"), fake.rules)
        assertTrue(marked.isEmpty())
        fake.fail = ApiException(409, ApiError("programme_ended", "x"), "x")
        recorder.recordOnce(programme)
        runCurrent()
        assertEquals("Already over", (recorder.state.value.outcome as RecordOutcome.Failed).error.title)
        recorder.reset()
        assertNull(recorder.state.value.outcome)
    }
}
