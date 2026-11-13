package com.streamhub.tv.core.data.room

import io.ktor.client.HttpClient
import io.ktor.client.plugins.auth.AuthCircuitBreaker
import io.ktor.client.plugins.timeout
import io.ktor.client.plugins.websocket.DefaultClientWebSocketSession
import io.ktor.client.plugins.websocket.webSocketSession
import io.ktor.websocket.CloseReason
import io.ktor.websocket.Frame
import io.ktor.websocket.close
import io.ktor.websocket.readText
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import javax.inject.Inject

/** Room sockets on the app's shared Ktor client (the WebSockets plugin is installed in HttpClientFactory). */
class KtorRoomSocketFactory @Inject constructor(private val client: HttpClient) : RoomSocketFactory {
    override suspend fun open(url: String): RoomSocket {
        val session = client.webSocketSession(url) {
            // The member token in the address is the credential; the bearer token stays off this request.
            attributes.put(AuthCircuitBreaker, Unit)
            // A room lasts as long as the film: neither the request nor the read may time out.
            timeout { requestTimeoutMillis = Long.MAX_VALUE; socketTimeoutMillis = Long.MAX_VALUE }
        }
        return KtorRoomSocket(session)
    }
}

private class KtorRoomSocket(private val session: DefaultClientWebSocketSession) : RoomSocket {
    override fun incoming(): Flow<String> = flow {
        for (frame in session.incoming) if (frame is Frame.Text) emit(frame.readText())
    }
    override suspend fun send(text: String) = session.send(Frame.Text(text))
    override suspend fun close(code: Short) = session.close(CloseReason(code, ""))
    override fun abort() = session.cancel()
    override suspend fun closeCode(): Short? = session.closeReason.await()?.code
}
