package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.TvAppRelease
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.security.MessageDigest

class UpdateRepositoryTest {
    private val sha = "ab".repeat(32)
    private fun release(code: Int, url: String = "/api/v1/app/tv/apk", sha256: String = sha, name: String = "1.2.0") =
        TvAppRelease(versionCode = code, versionName = name, apkUrl = url, sha256 = sha256)
    private fun repo(installed: Long = 4, server: String? = "http://hub.test:8787", fetch: suspend () -> TvAppRelease) =
        UpdateRepository(fetch, { installed }, { server })

    @Test fun newerBuildIsOfferedWithAnAbsoluteAddress() = runTest {
        val info = repo { release(5) }.check()
        assertEquals(UpdateInfo(5, "1.2.0", "http://hub.test:8787/api/v1/app/tv/apk", sha), info)
    }

    @Test fun sameOrOlderBuildIsNotAnUpdate() = runTest {
        assertNull(repo { release(4) }.check())
        assertNull(repo { release(3) }.check())
        assertNull(repo(installed = Long.MAX_VALUE) { release(9_999) }.check())
    }

    @Test fun serverWithoutAnUpdateServiceMeansNoUpdate() = runTest {
        assertNull(repo { throw ApiException(404, null, "HTTP 404") }.check())
    }

    @Test fun otherServerFailuresAreNotMistakenForNoUpdate() = runTest {
        try { repo { throw ApiException(503, null, "HTTP 503") }.check(); fail("503 must not be swallowed") }
        catch (e: ApiException) { assertEquals(503, e.status) }
    }

    @Test fun buildWithoutAUsableChecksumIsNotOffered() = runTest {
        assertNull(repo { release(5, sha256 = "") }.check())
        assertNull(repo { release(5, sha256 = "abc123") }.check())
        assertNull(repo { release(5, sha256 = "zz".repeat(32)) }.check())
        assertEquals(sha, repo { release(5, sha256 = "  ${sha.uppercase()}\n") }.check()?.sha256)
    }

    @Test fun addressesAreResolvedOrRefused() = runTest {
        assertEquals("https://downloads.example/tv.apk", repo { release(5, url = "https://downloads.example/tv.apk") }.check()?.apkUrl)
        assertNull(repo { release(5, url = "") }.check())
        assertNull(repo { release(5, url = "file:///sdcard/tv.apk") }.check())
        assertNull(repo { release(5, url = "//elsewhere.example/tv.apk") }.check())
        // A path needs a paired server to belong to.
        assertNull(repo(server = null) { release(5) }.check())
        assertEquals("http://hub.test:8787/app.apk", resolveApkUrl("/app.apk", "hub.test"))
    }

    @Test fun blankVersionNameFallsBackToTheCode() = runTest {
        assertEquals("5", repo { release(5, name = " ") }.check()?.versionName)
    }

    @Test fun releaseDecodesFromTheContractShape() {
        val r = StreamHubJson.decodeFromString(TvAppRelease.serializer(),
            """{"version_code":7,"version_name":"1.3.0","apk_url":"/api/v1/app/tv/apk","sha256":"$sha","notes":"ignored"}""")
        assertEquals(release(7, name = "1.3.0"), r)
        assertEquals(0, StreamHubJson.decodeFromString(TvAppRelease.serializer(), "{}").versionCode)
    }

    @Test fun tokenIsOnlyForThePairedServer() {
        assertTrue(sameOrigin("http://hub.test:8787/api/v1/app/tv/apk", "http://hub.test:8787"))
        assertTrue(sameOrigin("http://hub.test:8787/api/v1/app/tv/apk", "hub.test"))
        assertFalse(sameOrigin("https://downloads.example/tv.apk", "http://hub.test:8787"))
        assertFalse(sameOrigin("http://hub.test:8787.elsewhere.example/tv.apk", "http://hub.test:8787"))
        assertFalse(sameOrigin("http://hub.test:8787@elsewhere.example/tv.apk", "http://hub.test:8787"))
        assertFalse(sameOrigin("http://hub.test:8787/tv.apk", null))
    }

    @Test fun checksumHexMatchesAKnownVector() {
        val digest = MessageDigest.getInstance("SHA-256").digest("abc".toByteArray())
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", sha256Hex(digest))
        assertEquals(sha256Hex(digest), normalizeSha256("BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD"))
    }
}
