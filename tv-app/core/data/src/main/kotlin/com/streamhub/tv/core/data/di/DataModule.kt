package com.streamhub.tv.core.data.di

import android.content.Context
import android.content.pm.ApplicationInfo
import com.streamhub.tv.core.data.api.createStreamHubHttpClient
import com.streamhub.tv.core.data.auth.AccessTokenSource
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.WebSessionTokens
import dagger.Binds
import com.streamhub.tv.core.data.repo.SessionEnd
import com.streamhub.tv.core.data.repo.SessionEvents
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import io.ktor.client.HttpClient
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object DataModule {
    @Provides
    @Singleton
    fun httpClient(
        @ApplicationContext context: Context,
        sessionStore: SessionStore,
        events: SessionEvents,
        webTokens: AccessTokenSource,
    ): HttpClient = createStreamHubHttpClient(
        sessionStore = sessionStore,
        debug = (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0,
        onSessionExpired = { code -> events.emitEnded(SessionEnd.ofCode(code) ?: SessionEnd.Expired) },
        onUserSuspended = { events.emitEnded(SessionEnd.Suspended) },
        onTokensRefreshed = events::emitRefreshed,
        webTokens = webTokens,
    )
}

@Module
@InstallIn(SingletonComponent::class)
abstract class WebTokensModule {
    /** The web app shown by the shell owns the session; the native client asks it for tokens. */
    @Binds abstract fun accessTokens(impl: WebSessionTokens): AccessTokenSource
}
