package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.api.ApiException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import java.util.concurrent.atomic.AtomicBoolean

class AuthorizationOutcomeUnknown : Exception("Authorization outcome is uncertain. Sign in again; do not replay the PIN.")

/** A token-consuming POST and its local token commit form one bounded, non-cancellable operation. */
class AuthorizationCommitGate(private val changed: (Boolean)->Unit = {}) {
    private val active=AtomicBoolean(false)
    val finishing: Boolean get()=active.get()
    fun cancelIfIdle(cancel:()->Unit): Boolean {
        if(finishing) return false
        cancel();return true
    }
    suspend fun <T> finish(block:suspend()->T): T {
        currentCoroutineContext().ensureActive()
        check(active.compareAndSet(false,true)) {"Authorization already finishing"}
        changed(true)
        try {
            return withContext(NonCancellable) {
                try {withTimeout(20_000) {block()}}
                catch(_:TimeoutCancellationException) {throw AuthorizationOutcomeUnknown()}
                catch(e:ApiException) {if(e.status==0 || e.status>=500)throw AuthorizationOutcomeUnknown() else throw e}
                catch(_:java.io.IOException) {throw AuthorizationOutcomeUnknown()}
                catch(_:Exception) {throw AuthorizationOutcomeUnknown()}
            }
        } finally {active.set(false);changed(false)}
    }
}
