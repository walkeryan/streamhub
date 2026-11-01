package com.streamhub.tv.core.data.api

import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.parameter

/** null uses profile languages; an empty list explicitly includes every language. */
internal fun HttpRequestBuilder.applyLiveRegionLanguage(languages: List<String>?, country: String?) {
    languages?.let { parameter("language", if (it.isEmpty()) "all" else it.joinToString(",")) }
    country?.let { parameter("country", it) }
}
