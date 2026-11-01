package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.sameSwitchSession
import kotlinx.coroutines.Job
import java.util.concurrent.ConcurrentHashMap

/** Pending history writes belong to their original login generation, not the next active profile. */
class OwnedProgressJobs {
    private val jobs=ConcurrentHashMap<Job,Session>()
    fun track(job:Job,owner:Session) {jobs[job]=owner;job.invokeOnCompletion {jobs.remove(job)}}
    fun retainFor(current:Session?) {jobs.forEach {(job,owner)->if(current==null || !sameSwitchSession(owner,current))job.cancel()}}
    fun cancelAll() {jobs.keys.forEach {it.cancel()}}
}
