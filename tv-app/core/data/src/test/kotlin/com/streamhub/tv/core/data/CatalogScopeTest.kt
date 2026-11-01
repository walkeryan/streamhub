package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.model.Profile
import com.streamhub.tv.core.data.repo.catalogScope
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class CatalogScopeTest {
    private val original = Session("https://one.example", "access", "refresh", Profile("same-id", "One", languages = listOf("eng")))
    @Test fun sameProfileIdOnDifferentServersInvalidatesCatalog() {
        assertNotEquals(original.catalogScope(), original.copy(serverUrl = "https://two.example").catalogScope())
    }
    @Test fun preferredLanguageEditInvalidatesDefaultResults() {
        assertNotEquals(original.catalogScope(), original.copy(profile = original.profile!!.copy(languages = listOf("fra"))).catalogScope())
    }
    @Test fun tokenRefreshDoesNotReloadCatalog() {
        assertEquals(original.catalogScope(), original.copy(accessToken = "new-access", refreshToken = "new-refresh").catalogScope())
    }
    @Test fun profileChangeInvalidatesResultsAndLanguageOrderIsEquivalent() {
        assertNotEquals(original.catalogScope(), original.copy(profile = original.profile!!.copy(id = "other-id")).catalogScope())
        assertEquals(original.copy(profile = original.profile!!.copy(languages = listOf("fra", "eng"))).catalogScope(), original.copy(profile = original.profile!!.copy(languages = listOf("eng", "fra"))).catalogScope())
    }
}
