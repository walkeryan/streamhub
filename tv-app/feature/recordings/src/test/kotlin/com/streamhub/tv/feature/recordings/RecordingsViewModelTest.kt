package com.streamhub.tv.feature.recordings

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.Programme
import com.streamhub.tv.core.data.model.Recording
import com.streamhub.tv.core.data.model.RecordingRule
import com.streamhub.tv.core.data.repo.RecordingAvailability
import com.streamhub.tv.core.data.repo.RecordingsOverview
import com.streamhub.tv.core.data.repo.RecordingsRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class RecordingsViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private fun recording(id: String, state: String, startsAt: String, error: String? = null) =
        Recording(id = id, title = "Title $id", channelName = "One", startsAt = startsAt, endsAt = startsAt, state = state, error = error)

    private class Fake(
        var enabled: Boolean = true,
        var canSchedule: Boolean = true,
        var items: List<Recording> = emptyList(),
        var ruleList: List<RecordingRule> = emptyList(),
    ) : RecordingsRepository {
        var failList: Exception? = null
        var failRules: Exception? = null
        var failAction: Exception? = null
        val calls = mutableListOf<String>()
        override val availability = MutableStateFlow(RecordingAvailability())
        override suspend fun refreshAvailability() = availability.value
        override suspend fun recordings(): RecordingsOverview {
            failList?.let { throw it }
            return RecordingsOverview(RecordingAvailability(enabled, enabled && canSchedule), items)
        }
        override suspend fun rules(): List<RecordingRule> { failRules?.let { throw it }; return ruleList }
        override suspend fun record(programme: Programme): Recording = error("not used")
        override suspend fun addRule(kind: String, match: String, channelId: String?, newOnly: Boolean): RecordingRule = error("not used")
        override suspend fun remove(recordingId: String) { failAction?.let { throw it }; calls += "remove:$recordingId"; items = items.filterNot { it.id == recordingId } }
        override suspend fun setRuleEnabled(ruleId: String, enabled: Boolean): RecordingRule {
            failAction?.let { throw it }
            calls += "enable:$ruleId:$enabled"
            ruleList = ruleList.map { if (it.id == ruleId) it.copy(enabled = enabled) else it }
            return ruleList.first { it.id == ruleId }
        }
        override suspend fun deleteRule(ruleId: String) { failAction?.let { throw it }; calls += "deleteRule:$ruleId"; ruleList = ruleList.filterNot { it.id == ruleId } }
    }

    private val mixed = listOf(
        recording("late", "scheduled", "2026-11-15T20:00:00Z"),
        recording("old", "done", "2026-11-01T20:00:00Z"),
        recording("now", "recording", "2026-11-13T20:00:00Z"),
        recording("broken", "failed", "2026-11-10T20:00:00Z", error = "Source offline"),
        recording("gone", "cancelled", "2026-11-12T20:00:00Z"),
        recording("skipped", "missed", "2026-11-05T20:00:00Z", error = "Server was off"),
    )
    private val rules = listOf(RecordingRule(id = "s1", kind = "series", match = "The Long Show", newOnly = true), RecordingRule(id = "t1", kind = "team", match = "Lakers", enabled = false))

    @Test fun listsAreSplitIntoUpcomingRecordedAndRules() = runTest {
        val vm = RecordingsViewModel(Fake(items = mixed, ruleList = rules))
        val s = vm.state.value
        assertFalse(s.loading)
        assertTrue(s.enabled && s.canSchedule)
        // Upcoming earliest first; recorded newest first; cancelled ones are not listed.
        assertEquals(listOf("now", "late"), s.upcoming.map { it.id })
        assertEquals(listOf("broken", "skipped", "old"), s.recorded.map { it.id })
        assertEquals(listOf("s1", "t1"), s.rules.map { it.id })
        assertEquals("Source offline", s.recorded.first().error)
    }

    @Test fun aServerWithoutARecordingsFolderIsShownAsNotSetUp() = runTest {
        val fake = Fake(enabled = false).apply { failRules = IllegalStateException("rules must not be asked for") }
        val s = RecordingsViewModel(fake).state.value
        assertFalse(s.enabled)
        assertFalse(s.canSchedule)
        assertTrue(s.isEmpty)
        assertNull(s.error)
    }

    @Test fun aFailedLoadIsWordedAndCanBeRetried() = runTest {
        val fake = Fake(items = mixed).apply { failList = IOException("Connection refused") }
        val vm = RecordingsViewModel(fake)
        assertEquals("Can't reach the server", vm.state.value.error?.title)
        assertTrue(vm.state.value.error!!.canRetry)
        fake.failList = null
        vm.load()
        assertNull(vm.state.value.error)
        assertEquals(2, vm.state.value.upcoming.size)
    }

    @Test fun recordingsStillShowWhenOnlyTheRulesFail() = runTest {
        val fake = Fake(items = mixed).apply { failRules = IOException("down") }
        val s = RecordingsViewModel(fake).state.value
        assertNull(s.error)
        assertEquals(2, s.upcoming.size)
        assertTrue(s.rules.isEmpty())
    }

    @Test fun cancellingAnUpcomingRecordingRemovesItAndClosesTheDialog() = runTest {
        val fake = Fake(items = mixed)
        val vm = RecordingsViewModel(fake)
        vm.open(vm.state.value.upcoming.first { it.id == "late" })
        assertTrue(vm.state.value.selected is RecordingsSelection.Item)
        vm.removeSelected()
        assertEquals(listOf("remove:late"), fake.calls)
        assertEquals(listOf("now"), vm.state.value.upcoming.map { it.id })
        assertNull(vm.state.value.selected)
        assertEquals(1, vm.state.value.changes)
    }

    @Test fun deletingARecordedItemUsesTheSameCall() = runTest {
        val fake = Fake(items = mixed)
        val vm = RecordingsViewModel(fake)
        vm.open(vm.state.value.recorded.first { it.id == "old" })
        vm.removeSelected()
        assertEquals(listOf("remove:old"), fake.calls)
        assertEquals(listOf("broken", "skipped"), vm.state.value.recorded.map { it.id })
    }

    @Test fun aRefusedActionStaysInTheDialogWithItsReason() = runTest {
        val fake = Fake(items = mixed).apply { failAction = ApiException(409, ApiError("recording_unavailable", "x"), "x") }
        val vm = RecordingsViewModel(fake)
        vm.open(vm.state.value.upcoming.first())
        vm.removeSelected()
        val s = vm.state.value
        assertEquals("Recording isn't set up", s.actionError?.title)
        assertTrue(s.selected is RecordingsSelection.Item)
        assertFalse(s.busy)
        assertEquals(2, s.upcoming.size)
        // Dismissing clears the reason with the dialog.
        vm.dismiss()
        assertNull(vm.state.value.selected)
        assertNull(vm.state.value.actionError)
    }

    @Test fun rulesCanBeDisabledEnabledAndDeleted() = runTest {
        val fake = Fake(ruleList = rules)
        val vm = RecordingsViewModel(fake)
        vm.open(vm.state.value.rules.first { it.id == "s1" })
        vm.toggleSelectedRule()
        assertFalse(vm.state.value.rules.first { it.id == "s1" }.enabled)
        vm.open(vm.state.value.rules.first { it.id == "t1" })
        vm.toggleSelectedRule()
        assertTrue(vm.state.value.rules.first { it.id == "t1" }.enabled)
        vm.open(vm.state.value.rules.first { it.id == "t1" })
        vm.deleteSelectedRule()
        assertEquals(listOf("enable:s1:false", "enable:t1:true", "deleteRule:t1"), fake.calls)
        assertEquals(listOf("s1"), vm.state.value.rules.map { it.id })
    }

    @Test fun someoneWhoMayOnlyLookCannotChangeAnything() = runTest {
        val fake = Fake(canSchedule = false, items = mixed, ruleList = rules)
        val vm = RecordingsViewModel(fake)
        assertFalse(vm.state.value.canSchedule)
        vm.open(vm.state.value.upcoming.first())
        vm.removeSelected()
        vm.open(vm.state.value.rules.first())
        vm.toggleSelectedRule()
        vm.deleteSelectedRule()
        assertTrue(fake.calls.isEmpty())
    }

    @Test fun aGamesSidesLeadTheLineUnderItsTitle() {
        val game = Recording(id = "g1", title = "League: Northside at Harbour City", channelName = "One", teams = listOf("Northside", "Harbour City"), state = "done")
        assertEquals("Northside vs Harbour City · One", recordingByline(game))
        val episode = Recording(id = "e1", title = "The Long Show", channelName = "Two", season = 2, episode = 5, subtitle = "The One After", state = "done")
        assertEquals("Two · S2 E5 · The One After", recordingByline(episode))
        assertEquals("", recordingByline(Recording(id = "x", state = "done")))
    }
}
