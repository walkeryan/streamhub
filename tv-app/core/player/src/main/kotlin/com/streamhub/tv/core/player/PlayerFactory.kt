package com.streamhub.tv.core.player

import android.content.Context
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.api.StreamHubApi
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/** Creates player instances; ViewModels own and release them. */
@Singleton
class PlayerFactory @Inject constructor(
    @ApplicationContext private val context: Context,
    private val api: StreamHubApi,
    private val sessions: SessionStore,
) {
    fun create(reportProgress: Boolean = true) = StreamHubPlayer(context, api, sessions, reportProgress)
}
