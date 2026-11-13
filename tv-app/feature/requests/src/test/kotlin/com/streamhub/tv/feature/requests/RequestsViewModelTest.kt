package com.streamhub.tv.feature.requests

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.model.ApiError
import com.streamhub.tv.core.data.model.MediaRequest
import com.streamhub.tv.core.data.model.Notice
import com.streamhub.tv.core.data.repo.RequestList
import com.streamhub.tv.core.data.repo.RequestTarget
import com.streamhub.tv.core.data.repo.RequestsRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
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
class RequestsViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private fun request(id: String, state: String, who: String = "Sam") =
        MediaRequest(id = id, profileName = who, mediaType = "movie", tmdbId = 603, titleId = "tmdb:movie:603", title = "Title $id", state = state)

    private class Fake(var canApprove: Boolean, var mine: List<MediaRequest>, var everyone: List<MediaRequest> = emptyList()) : RequestsRepository {
        var failMine: Exception? = null
        var failAll: Exception? = null
        var failDecision: Exception? = null
        val calls = mutableListOf<String>()
        override suspend fun mine(): RequestList { failMine?.let { throw it }; calls += "mine"; return RequestList(mine, canApprove) }
        override suspend fun all(): List<MediaRequest> { failAll?.let { throw it }; calls += "all"; return everyone }
        override suspend fun request(target: RequestTarget, seasons: List<Int>?): MediaRequest = error("not used")
        override suspend fun approve(requestId: String): MediaRequest { failDecision?.let { throw it }; calls += "approve:$requestId"; return everyone.first { it.id == requestId }.copy(state = "sent") }
        override suspend fun decline(requestId: String): MediaRequest { failDecision?.let { throw it }; calls += "decline:$requestId"; return everyone.first { it.id == requestId }.copy(state = "declined") }
        override suspend fun unreadNotices(): List<Notice> = emptyList()
        override suspend fun markRead(noticeId: String) = Unit
    }

    private val mine = listOf(request("a", "pending_approval"), request("b", "available"))
    private val everyone = mine + listOf(request("c", "pending_approval", who = "Alex"), request("d", "declined", who = "Alex"))

    @Test fun someoneWhoMayOnlyAskSeesTheirOwnRequestsAndNoOthers() = runTest {
        val fake = Fake(canApprove = false, mine = mine)
        val vm = RequestsViewModel(fake)
        val s = vm.state.value
        assertFalse(s.loading)
        assertFalse(s.canApprove)
        assertEquals(listOf("a", "b"), s.shown.map { it.id })
        assertEquals(listOf("mine"), fake.calls)
        // "All requests" is not theirs to open.
        vm.show(RequestsTab.All)
        assertEquals(RequestsTab.Mine, vm.state.value.tab)
    }

    @Test fun anApproverAlsoGetsEveryonesRequests() = runTest {
        val vm = RequestsViewModel(Fake(canApprove = true, mine = mine, everyone = everyone))
        assertTrue(vm.state.value.canApprove)
        assertEquals(2, vm.state.value.waiting)
        vm.show(RequestsTab.All)
        assertEquals(listOf("a", "b", "c", "d"), vm.state.value.shown.map { it.id })
    }

    @Test fun approvingSendsTheRequestOnAndUpdatesBothLists() = runTest {
        val fake = Fake(canApprove = true, mine = mine, everyone = everyone)
        val vm = RequestsViewModel(fake)
        vm.open(everyone.first { it.id == "a" })
        vm.approveSelected()
        assertTrue("approve:a" in fake.calls)
        assertEquals("sent", vm.state.value.mine.first { it.id == "a" }.state)
        assertEquals("sent", vm.state.value.all.first { it.id == "a" }.state)
        assertNull(vm.state.value.selected)
        assertEquals(1, vm.state.value.waiting)
    }

    @Test fun decliningMarksTheRequestDeclined() = runTest {
        val fake = Fake(canApprove = true, mine = mine, everyone = everyone)
        val vm = RequestsViewModel(fake)
        vm.open(everyone.first { it.id == "c" })
        vm.declineSelected()
        assertTrue("decline:c" in fake.calls)
        assertEquals("declined", vm.state.value.all.first { it.id == "c" }.state)
    }

    @Test fun onlyAWaitingRequestCanBeDecidedAndOnlyByAnApprover() = runTest {
        val approver = Fake(canApprove = true, mine = mine, everyone = everyone)
        val vm = RequestsViewModel(approver)
        vm.open(everyone.first { it.id == "d" })   // already declined
        vm.approveSelected()
        vm.declineSelected()
        assertFalse(approver.calls.any { it.startsWith("approve") || it.startsWith("decline") })

        val asker = Fake(canApprove = false, mine = mine, everyone = everyone)
        val other = RequestsViewModel(asker)
        other.open(mine.first())
        other.approveSelected()
        assertEquals(listOf("mine"), asker.calls)
    }

    @Test fun aDecisionSomeoneElseAlreadyMadeIsExplainedInTheDialog() = runTest {
        val fake = Fake(canApprove = true, mine = mine, everyone = everyone).apply { failDecision = ApiException(409, ApiError("conflict", "This request was already decided."), "x") }
        val vm = RequestsViewModel(fake)
        vm.open(everyone.first { it.id == "c" })
        vm.approveSelected()
        val s = vm.state.value
        assertEquals("This request was already decided.", s.actionError?.body)
        assertEquals("c", s.selected?.id)
        assertFalse(s.busy)
    }

    @Test fun aFailedLoadIsWordedAndCanBeRetried() = runTest {
        val fake = Fake(canApprove = false, mine = mine).apply { failMine = IOException("Connection refused") }
        val vm = RequestsViewModel(fake)
        assertEquals("Can't reach the server", vm.state.value.error?.title)
        fake.failMine = null
        vm.load()
        assertNull(vm.state.value.error)
        assertEquals(2, vm.state.value.mine.size)
    }

    @Test fun ifEveryonesListIsRefusedTheUserIsNotTreatedAsAnApprover() = runTest {
        val fake = Fake(canApprove = true, mine = mine).apply { failAll = ApiException(403, ApiError("forbidden", "no"), "no") }
        val s = RequestsViewModel(fake).state.value
        assertFalse(s.canApprove)
        assertNull(s.error)
        assertEquals(2, s.mine.size)
    }
}
