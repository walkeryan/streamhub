package com.streamhub.tv.feature.player

import kotlinx.coroutines.CancellationException

/**
 * A stream request answered 403 `playback_paused`. That is what an administrator's stop looks like,
 * and also what a stream issued before a server restart looks like. The first such answer for a
 * playback is therefore not put to the viewer: it is resolved again, once, without a word. Only a
 * second one, or a refusal of that fresh resolve, is the real stop.
 */
internal class PausedRetry {
    private var spent = false

    /** True once per playback: the caller then recovers silently. False afterwards: the stop is shown. */
    fun claim(): Boolean {
        if (spent) return false
        spent = true
        return true
    }

    /** The picture is moving again, or another title or channel was started: the next stop is a first one. */
    fun reset() { spent = false }
}

/**
 * The one silent recovery: a fresh GET /playback/generation, then one resolve that carries it.
 * A server that really is stopped refuses the generation itself, and [resolve] is then never called.
 * Any failure is returned as the answer to show.
 */
internal suspend fun <T> resumeWithFreshGeneration(generation: suspend () -> String, resolve: suspend (String) -> T): Result<T> =
    try { Result.success(resolve(generation())) }
    catch (e: CancellationException) { throw e }
    catch (e: Exception) { Result.failure(e) }
