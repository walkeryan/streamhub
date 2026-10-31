package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.auth.normalizeServerUrl
import com.streamhub.tv.core.data.model.HomeResponse
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import com.streamhub.tv.core.data.model.PlaybackMode
import com.streamhub.tv.core.data.model.PlaybackRequest
import com.streamhub.tv.core.data.model.RowLayout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ContractTest {
    @Test fun normalizesServerUrls() {
        assertEquals("http://192.168.1.10:8787", normalizeServerUrl("192.168.1.10"))
        assertEquals("http://192.168.1.10:8787", normalizeServerUrl("http://192.168.1.10:8787/"))
        assertEquals("http://tower:9000", normalizeServerUrl("tower:9000"))
        assertEquals("https://hub.example.com", normalizeServerUrl("https://hub.example.com/api/v1"))
    }

    @Test fun decodesHandoffDescriptor() {
        val d = StreamHubJson.decodeFromString(
            PlaybackDescriptor.serializer(),
            """{"id":"tmdb:movie:603","mode":"handoff","title":"The Matrix","unknown_future_field":1,
               "handoff":{"service_key":"netflix","android_package":"com.netflix.ninja",
               "android_uri":"https://www.netflix.com/title/20557937?source=30","android_extras":{},
               "web_url":"https://www.netflix.com/title/20557937","supports_title_link":true}}""",
        )
        assertEquals(PlaybackMode.HANDOFF, d.mode)
        assertEquals("com.netflix.ninja", d.handoff?.androidPackage)
    }

    @Test fun decodesLiveDescriptorWithChannelContext() {
        val d = StreamHubJson.decodeFromString(
            PlaybackDescriptor.serializer(),
            """{"id":"ch:src1:42","mode":"proxy","url":"http://h/api/v1/stream/abc","mime":"video/mp2t","live":true,
               "headers":{"User-Agent":"VLC"},"channel_context":{"list_id":"L1","index":4,"count":120}}""",
        )
        assertTrue(d.live)
        assertEquals(4, d.channelContext?.index)
        assertEquals("VLC", d.headers["User-Agent"])
    }

    @Test fun homeDefaultsLayout() {
        val h = StreamHubJson.decodeFromString(HomeResponse.serializer(), """{"rows":[{"id":"r","title":"T","tiles":[]}]}""")
        assertEquals(RowLayout.POSTER, h.rows.single().layout)
    }

    @Test fun playbackRequestIdentifiesClient() {
        val s = StreamHubJson.encodeToString(PlaybackRequest.serializer(), PlaybackRequest(id = "x"))
        assertTrue(s.contains("\"client\":\"android_tv\""))
    }
}
