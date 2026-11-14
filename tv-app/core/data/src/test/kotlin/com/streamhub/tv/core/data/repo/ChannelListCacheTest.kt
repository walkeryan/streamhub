package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.model.Channel
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ChannelListCacheTest {
    private fun ch(n: Int) = Channel(id = "ch:s:$n", sourceId = "s", number = n, name = "Channel $n", group = "g")
    private val collegeFootball = listOf(ch(1922), ch(1965), ch(1992))
    private val everything = listOf(ch(1107), ch(1922), ch(1965), ch(1992))

    @Test fun aListWithoutThePlayingChannelIsPassedOverForOneThatHasIt() = runTest {
        // FOX 62 opened from search while the last list browsed was a sports group it is not in.
        val asked = mutableListOf<String>()
        val list = zapListFor("ch:s:1107",
            { asked += "remembered"; collegeFootball },
            { asked += "server"; everything },
            { asked += "last browsed"; collegeFootball },
        )
        assertEquals(everything, list)
        assertEquals(listOf("remembered", "server"), asked)
    }

    @Test fun noListHoldsTheChannel_noneIsUsed() = runTest {
        assertNull(zapListFor("ch:s:1107", { collegeFootball }, { null }, { emptyList() }))
    }

    @Test fun theFirstListHoldingTheChannelIsUsedWithoutAskingFurther() = runTest {
        var asked = false
        assertEquals(collegeFootball, zapListFor("ch:s:1965", { collegeFootball }, { asked = true; everything }))
        assertEquals(false, asked)
    }
}
