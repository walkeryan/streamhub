package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.api.StreamHubJson
import com.streamhub.tv.core.data.model.PlaybackDescriptor
import org.junit.Assert.*
import org.junit.Test

class RuntimeAudioContractTest {
    @Test fun runtimeGuardFieldsPreserveArchiveAndBufferContract() {
        val value=StreamHubJson.decodeFromString<PlaybackDescriptor>("""{"id":"ch:fixture:1","mode":"direct","url":"https://fixture.invalid/media","live":false,"buffer_available":true,"archive_start":"2026-11-01T12:00:00Z","archive_end":"2026-11-01T13:00:00Z","preferred_audio_languages":["eng"],"audio_language_required":true}""")
        assertTrue(value.audioLanguageRequired);assertEquals(listOf("eng"),value.preferredAudioLanguages)
        assertTrue(value.bufferAvailable);assertFalse(value.live);assertNotNull(value.archiveStart)
    }
}
