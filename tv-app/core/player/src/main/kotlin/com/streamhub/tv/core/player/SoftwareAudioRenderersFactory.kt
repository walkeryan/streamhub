package com.streamhub.tv.core.player

import android.content.Context
import android.os.Handler
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.Renderer
import androidx.media3.exoplayer.audio.AudioRendererEventListener
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.mediacodec.MediaCodecSelector

/**
 * [DefaultRenderersFactory] plus the optional prebuilt FFmpeg audio renderer.
 *
 * The renderer is only on the classpath when the app is built with `-Pstreamhub.ffmpegDecoder=true`
 * (it is GPL-3.0, so the default build leaves it out). It is looked up by name: this file compiles
 * and behaves like the stock factory without it. When present it goes ahead of the platform audio
 * decoders, so a TV that lacks AC-3/E-AC-3/DTS, or advertises a decoder that outputs silence, still
 * has sound; the cost is that audio is decoded to PCM here rather than passed through to a receiver.
 * Video is never decoded in software.
 */
@OptIn(UnstableApi::class)
internal class SoftwareAudioRenderersFactory(context: Context) : DefaultRenderersFactory(context) {
    override fun buildAudioRenderers(
        context: Context,
        extensionRendererMode: Int,
        mediaCodecSelector: MediaCodecSelector,
        enableDecoderFallback: Boolean,
        audioSink: AudioSink,
        eventHandler: Handler,
        eventListener: AudioRendererEventListener,
        out: ArrayList<Renderer>,
    ) {
        val first = out.size
        super.buildAudioRenderers(context, extensionRendererMode, mediaCodecSelector, enableDecoderFallback, audioSink, eventHandler, eventListener, out)
        softwareAudioRenderer(eventHandler, eventListener, audioSink)?.let { out.add(first, it) }
    }

    private fun softwareAudioRenderer(handler: Handler, listener: AudioRendererEventListener, sink: AudioSink): Renderer? =
        // Any failure (class absent, constructor changed in a newer artifact, native library missing
        // for this ABI) means "no software decoder": playback carries on with the platform ones.
        runCatching {
            Class.forName(FFMPEG_AUDIO_RENDERER)
                .getConstructor(Handler::class.java, AudioRendererEventListener::class.java, AudioSink::class.java)
                .newInstance(handler, listener, sink) as Renderer
        }.getOrNull()

    private companion object {
        /** In the artifact named by `media3-ffmpeg-decoder` in gradle/libs.versions.toml; kept by proguard-rules.pro. */
        const val FFMPEG_AUDIO_RENDERER = "io.github.anilbeesetti.nextlib.media3ext.ffdecoder.FfmpegAudioRenderer"
    }
}
