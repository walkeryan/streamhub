package com.streamhub.tv.core.data.model

/** One immutable snapshot shared by Channels and Guide; region never implies a language. */
data class LiveGuideFilters(
    val country: String = "US",
    val languages: List<String> = listOf("eng"),
    val group: String? = null,
    val favorites: Boolean = false,
)
