package com.streamhub.tv

import android.app.Application
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import coil3.disk.DiskCache
import coil3.disk.directory
import coil3.memory.MemoryCache
import coil3.network.ktor3.KtorNetworkFetcherFactory
import coil3.request.crossfade
import dagger.hilt.android.HiltAndroidApp
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO

@HiltAndroidApp
class StreamHubApp : Application(), SingletonImageLoader.Factory {

    /**
     * Coil 3 image loader: memory cache capped at 15 % of the app heap (2 GB sticks), 128 MB disk
     * cache, its own CIO client (artwork comes from TMDB/Plex/IPTV logo hosts; no bearer token).
     */
    override fun newImageLoader(context: PlatformContext): ImageLoader =
        ImageLoader.Builder(context)
            .components { add(KtorNetworkFetcherFactory(httpClient = { HttpClient(CIO) })) }
            .memoryCache { MemoryCache.Builder().maxSizePercent(context, 0.15).build() }
            .diskCache { DiskCache.Builder().directory(cacheDir.resolve("img")).maxSizeBytes(128L * 1024 * 1024).build() }
            .crossfade(true)
            .build()
}
