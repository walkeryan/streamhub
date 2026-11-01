package com.streamhub.tv.core.data.repo

import org.junit.Assert.*
import org.junit.Test

class RecentSearchScopeTest {
    @Test fun identicalProfileIdsOnDifferentServersNeverShareHistory() {
        val one=recentSearchStorageKey("https://one.invalid","default")!!
        val two=recentSearchStorageKey("https://two.invalid","default")!!
        val storage=mapOf(one to listOf("private query"),"default.recent_searches" to listOf("legacy unknown origin"))
        assertNotEquals(one,two)
        assertEquals(listOf("private query"),storage[one])
        assertNull(storage[two])
        assertNotEquals(one,recentSearchStorageKey("https://one.invalid","child"))
    }
    @Test fun canonicalServerIdentityOmitsCredentialsAndQuery() {
        val clean=recentSearchStorageKey("https://one.invalid","default")!!
        assertEquals(clean,recentSearchStorageKey("https://user:secret@ONE.invalid:443/api/v1/?token=private#fragment","default"))
        assertFalse(clean.contains("secret"));assertFalse(clean.contains("private"));assertFalse(clean.contains("user"))
        assertNull(recentSearchStorageKey("not a server","default"))
    }
}
