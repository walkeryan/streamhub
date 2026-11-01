package com.streamhub.tv.core.data.api

import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.parameter

/** null follows profile preferences; empty explicitly requests every language; und means unknown. */
internal fun HttpRequestBuilder.applySearchFilters(languages: List<String>?, sourceId: String?) {
    languages?.let { parameter("language", if (it.isEmpty()) "all" else it.joinToString(",")) }
    sourceId?.let { parameter("source_id", it) }
}
