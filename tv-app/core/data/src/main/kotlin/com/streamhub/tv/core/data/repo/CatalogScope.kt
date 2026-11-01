package com.streamhub.tv.core.data.repo

import com.streamhub.tv.core.data.auth.Session

/** Catalog defaults and saved filters belong to a server/profile, not just a profile ID. */
data class CatalogScope(val server: String?, val profileId: String?, val languages: List<String>)
fun Session.catalogScope() = CatalogScope(apiBase, profile?.id, profile?.languages.orEmpty().sorted())
