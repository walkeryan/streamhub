package com.streamhub.tv.shell

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.model.Profile
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ShellProtocolTest {
    private val server = "http://192.0.2.10:8787"

    private fun title(json: String) = (parseShellPlay(json) as ParsedPlay.Ok).play as ShellPlay.Title
    private fun refused(json: String) = parseShellPlay(json) is ParsedPlay.Rejected

    // ---- play(json) -----------------------------------------------------------------------------

    @Test fun aTitleCarriesEverythingTheWebPlayerWouldHaveUsed() {
        val t = title("""{"v":1,"kind":"title","id":"tmdb:movie:603","source_id":"plex-1","variant_id":"v2","allow_unverified_audio":true,"start_position_ms":120000,"audio_track":1,"future_field":"ignored"}""")
        assertEquals("tmdb:movie:603", t.request.id)
        assertEquals("plex-1", t.request.sourceId)
        assertEquals("v2", t.request.variantId)
        assertTrue(t.request.allowUnverifiedAudio)
        assertEquals("the TV resolves for itself", "android_tv", t.request.client)
        assertEquals(120_000L, t.startPositionMs)
    }

    @Test fun catchUpRewindAndRecordingsAreResolvedAsTheyAre() {
        assertEquals("prog:9", title("""{"v":1,"kind":"title","id":"ch:1","catchup_programme_id":"prog:9"}""").request.catchupProgrammeId)
        assertEquals("2026-11-13T20:00:00Z", title("""{"v":1,"kind":"title","id":"ch:1","rewind_at":"2026-11-13T20:00:00Z"}""").request.rewindAt)
        assertEquals("rec:42", title("""{"v":1,"kind":"title","id":"rec:42"}""").request.id)
        assertFalse("consent without a chosen variant applies to nothing", title("""{"v":1,"kind":"title","id":"x:1","allow_unverified_audio":true}""").request.allowUnverifiedAudio)
        assertNull(title("""{"v":1,"kind":"title","id":"x:1","source_id":null,"start_position_ms":null}""").startPositionMs)
    }

    @Test fun aRoomIsItsCodeAndTheSeatsMemberToken() {
        val r = (parseShellPlay("""{"v":1,"kind":"room","room_code":"abc234","member_token":"mt.abc-123","allow_unverified_audio":true}""") as ParsedPlay.Ok).play as ShellPlay.Room
        assertEquals("ABC234", r.code)
        assertEquals("mt.abc-123", r.memberToken)
        assertTrue(r.allowUnverifiedAudio)
        assertFalse("the token stays out of logs", r.toString().contains("mt.abc-123"))
    }

    @Test fun anythingMalformedIsRefused() {
        assertTrue("not JSON", refused("play"))
        assertTrue("not an object", refused("[1]"))
        assertTrue("no version", refused("""{"kind":"title","id":"x:1"}"""))
        assertTrue("another version", refused("""{"v":2,"kind":"title","id":"x:1"}"""))
        assertTrue("version as a string", refused("""{"v":"1","kind":"title","id":"x:1"}"""))
        assertTrue("unknown kind", refused("""{"v":1,"kind":"url","id":"x:1"}"""))
        assertTrue("no id", refused("""{"v":1,"kind":"title"}"""))
        assertTrue("an id with spaces", refused("""{"v":1,"kind":"title","id":"x 1"}"""))
        assertTrue("an id with a control character", refused("""{"v":1,"kind":"title","id":"x\n1"}"""))
        assertTrue("an id too long", refused("""{"v":1,"kind":"title","id":"${"a".repeat(513)}"}"""))
        assertTrue("a number for an id", refused("""{"v":1,"kind":"title","id":603}"""))
        assertTrue("a string for a boolean", refused("""{"v":1,"kind":"title","id":"x:1","variant_id":"v","allow_unverified_audio":"yes"}"""))
        assertTrue("a negative start", refused("""{"v":1,"kind":"title","id":"x:1","start_position_ms":-1}"""))
        assertTrue("a start as a string", refused("""{"v":1,"kind":"title","id":"x:1","start_position_ms":"10"}"""))
        assertTrue("a fraction", refused("""{"v":1,"kind":"title","id":"x:1","start_position_ms":1.5}"""))
        assertTrue("not a date", refused("""{"v":1,"kind":"title","id":"ch:1","rewind_at":"yesterday"}"""))
        assertTrue("catch-up and rewind together", refused("""{"v":1,"kind":"title","id":"ch:1","catchup_programme_id":"p","rewind_at":"2026-11-13T20:00:00Z"}"""))
        assertTrue("a room code with look-alikes", refused("""{"v":1,"kind":"room","room_code":"ABC10O","member_token":"t"}"""))
        assertTrue("a short room code", refused("""{"v":1,"kind":"room","room_code":"ABC","member_token":"t"}"""))
        assertTrue("no member token", refused("""{"v":1,"kind":"room","room_code":"ABC234"}"""))
        assertTrue("a member token with spaces", refused("""{"v":1,"kind":"room","room_code":"ABC234","member_token":"a b"}"""))
        assertTrue("too long", refused("""{"v":1,"kind":"title","id":"x:1","pad":"${"a".repeat(17_000)}"}"""))
    }

    @Test fun aStreamAddressFromThePageIsNeverUsed() {
        // There is no field for one: an url is ignored like any unknown field and the TV resolves the id.
        val t = title("""{"v":1,"kind":"title","id":"x:1","url":"http://198.51.100.7/evil.m3u8"}""")
        assertEquals(com.streamhub.tv.core.data.model.PlaybackRequest(id = "x:1"), t.request)
    }

    // ---- Seeding the web app's session ----------------------------------------------------------

    @Test fun theSeededSessionHasTheShapeSessionTsReads() {
        val profile = Profile("p1", "Sam \"the\" viewer", languages = listOf("en"))
        val json = webSessionJson("A1", "R1", profile)
        val root = StreamHubJson.parseToJsonElement(json).jsonObject
        assertEquals(setOf("access", "refresh", "profile"), root.keys)
        assertEquals("A1", root["access"]!!.jsonPrimitive.content)
        assertEquals("R1", root["refresh"]!!.jsonPrimitive.content)
        val p = root["profile"]!!.jsonObject
        assertEquals("p1", p["id"]!!.jsonPrimitive.content)
        assertEquals("Sam \"the\" viewer", p["name"]!!.jsonPrimitive.content)
        assertEquals("en", p["languages"]!!.jsonArray.single().jsonPrimitive.content)
    }

    @Test fun theSeedScriptWritesExactlyThatValueUnderTheWebAppsKey() {
        val json = webSessionJson("A1", "R1", Profile("p1", "O'Brien </script>"))
        val script = seedScript(json)
        assertTrue(script.contains("localStorage.setItem(\"sh.session.v1\","))
        // The value is one JavaScript string literal: decoding it gives the JSON back unchanged.
        val literal = Regex("setItem\\(\"sh\\.session\\.v1\",(\".*?(?<!\\\\)\")\\)").find(script)!!.groupValues[1]
        assertEquals(json, (StreamHubJson.parseToJsonElement(literal) as JsonPrimitive).content)
        assertEquals(WEB_SESSION_KEY, "sh.session.v1")
    }

    @Test fun aTokenRequestNamesItsIdAndTheRefusedToken() {
        assertEquals(
            "(function(){var s=window.streamhubShell;if(!s||typeof s.token!=='function')return false;s.token(\"abc\",\"tok\\\"en\");return true})()",
            tokenRequestScript("abc", "tok\"en"),
        )
        assertTrue(tokenRequestScript("abc", null).contains("s.token(\"abc\",null)"))
    }

    // ---- Which addresses may load -----------------------------------------------------------------

    @Test fun onlyThePairedServersOriginLoads() {
        assertTrue(mayLoad(server, "http://192.0.2.10:8787/"))
        assertTrue(mayLoad(server, "http://192.0.2.10:8787/title/tmdb%3Amovie%3A603?x=1#y"))
        assertTrue("the address as it was typed", mayLoad("192.0.2.10", "http://192.0.2.10:8787/live"))
        assertTrue("case of the host", mayLoad("http://Hub.Example:8787", "http://hub.example:8787/"))
        assertTrue(mayLoad("https://hub.example", "https://hub.example:443/x"))
        assertFalse("another port", mayLoad(server, "http://192.0.2.10:8788/"))
        assertFalse("another host", mayLoad(server, "http://198.51.100.7:8787/"))
        assertFalse("another scheme", mayLoad(server, "https://192.0.2.10:8787/"))
        assertFalse("a streaming service's site", mayLoad(server, "https://www.example.com/watch/1"))
        assertFalse(mayLoad(server, "intent://play#Intent;package=x;end"))
        assertFalse(mayLoad(server, "javascript:alert(1)"))
        assertFalse(mayLoad(server, "file:///sdcard/x.html"))
        assertFalse(mayLoad(server, "data:text/html,hi"))
        assertFalse("credentials in the address", mayLoad(server, "http://user@192.0.2.10:8787/"))
        assertFalse(mayLoad(server, "not a url"))
        assertEquals("http://192.0.2.10:8787/", webAppUrl("192.0.2.10"))
    }

    // ---- Back -------------------------------------------------------------------------------------

    @Test fun backLeavesTheAppOnlyWhenTheWebAppHadNothingToGoBackTo() {
        assertEquals("closed a dialog or went back", BackOutcome.Consumed, backOutcome("true"))
        assertEquals("its home screen", BackOutcome.Exit, backOutcome("false"))
        assertEquals("no web app in the page", BackOutcome.Exit, backOutcome("null"))
        assertEquals(BackOutcome.Exit, backOutcome(null))
        assertEquals(BackOutcome.Exit, backOutcome("\"true\""))
    }

    @Test fun nativeScreensCoverTheWebAppOnlyWhenTheyMustAndBackStaysTheirs() {
        val owned = Session(server, "A1", null, Profile("p1", "Sam"), "g1", webOwned = true)
        assertEquals(ShellScreen.Loading, shellScreen(null, false, false, false))
        assertEquals(ShellScreen.Web, shellScreen(owned, false, false, false))
        assertEquals("the pair is written into the web app first, behind the same screen", ShellScreen.Web, shellScreen(owned.copy(refreshToken = "R1", webOwned = false), false, false, false))
        assertEquals(ShellScreen.Unreachable, shellScreen(owned, false, true, false))
        assertEquals(ShellScreen.Settings, shellScreen(owned, false, true, true))
        assertEquals(ShellScreen.Pairing, shellScreen(owned, true, true, false))
        assertEquals("signed out", ShellScreen.Pairing, shellScreen(owned.copy(webOwned = false, accessToken = null, profile = null), false, false, false))
        assertEquals("no server", ShellScreen.Pairing, shellScreen(Session(null, null, null, null), false, false, false))
    }

    @Test fun capabilitiesSayWhatTheShellIs() {
        val c = StreamHubJson.parseToJsonElement(capabilitiesJson("1.2.0", 7, listOf("hdr10"), listOf("hevc"), listOf("eac3"))) as JsonObject
        assertEquals(BRIDGE_VERSION, c["bridge"]!!.jsonPrimitive.content.toInt())
        assertEquals("1.2.0", c["shell"]!!.jsonPrimitive.content)
        assertEquals("true", c["native_playback"]!!.jsonPrimitive.content)
        assertEquals("hdr10", c["hdr"]!!.jsonArray.single().jsonPrimitive.content)
        assertEquals("eac3", c["audio_passthrough"]!!.jsonArray.single().jsonPrimitive.content)
    }
}
