package com.streamhub.tv.feature.search

import com.streamhub.tv.core.data.model.Channel
import com.streamhub.tv.core.data.model.SearchProgramme
import com.streamhub.tv.core.data.model.SearchResponse
import com.streamhub.tv.core.data.model.Tile
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged

/** The three result rows, in the order they are shown unless the server says otherwise. */
internal enum class SearchSectionName(val wire: String, val heading: String) {
    PROGRAMMES("programmes", "On TV"),
    CHANNELS("channels", "Channels"),
    TITLES("titles", "Movies & Shows"),
    ;

    companion object {
        fun of(wire: String): SearchSectionName? = entries.firstOrNull { it.wire == wire }
    }
}

internal data class SearchScope(val server: String?, val profileId: String?, val preferredLanguages: List<String>)

/** [expand]: the sections asked for in full ("Show more"). It belongs to the query: a new query starts capped. */
internal data class SearchRequest(val scope: SearchScope, val query: String, val expand: Set<SearchSectionName> = emptySet()) {
    /** The same search, whatever was asked for in full: its rows stay on screen while more of them load. */
    fun sameSearch(other: SearchRequest?): Boolean = other != null && other.scope == scope && other.query == query
}

/**
 * What the screen shows for one query: the three rows, the order they stand in and which of them
 * the server holds more of. Built from two answers (see [withLocal], [withFull]); nothing that is
 * on screen is ever moved or reordered by a later answer, rows are only added at the end.
 */
internal data class SearchView(
    val programmes: List<SearchProgramme> = emptyList(),
    val channels: List<Channel> = emptyList(),
    val titles: List<Tile> = emptyList(),
    val order: List<SearchSectionName> = SearchSectionName.entries,
    val more: Set<SearchSectionName> = emptySet(),
    val attribution: String? = null,
) {
    val found: Int get() = programmes.size + channels.size + titles.size
}

/** The rows that are shown keep their places (each refreshed from [incoming] by its key); what is new goes after them. */
internal fun <T> appendUnseen(shown: List<T>, incoming: List<T>, key: (T) -> String): List<T> {
    val latest = incoming.associateBy(key)
    val have = HashSet<String>()
    val kept = shown.map { latest[key(it)] ?: it }.filter { have.add(key(it)) }
    return kept + incoming.filter { have.add(key(it)) }
}

/**
 * The order of the rows: the server's when it gives one, else On TV, Channels, Movies & Shows with
 * Channels first when the query is a listed channel's number or whole name.
 */
internal fun searchOrder(order: List<String>, query: String, channels: List<Channel>): List<SearchSectionName> {
    val named = order.mapNotNull(SearchSectionName::of).distinct()
    if (named.isNotEmpty()) return named + SearchSectionName.entries.filterNot { it in named }
    val q = query.trim()
    val exact = q.isNotEmpty() && channels.any { it.name.trim().equals(q, ignoreCase = true) || (q.toIntOrNull() != null && it.number == q.toIntOrNull()) }
    return if (exact) listOf(SearchSectionName.CHANNELS, SearchSectionName.PROGRAMMES, SearchSectionName.TITLES) else SearchSectionName.entries
}

private fun SearchResponse.moreOf(): Set<SearchSectionName> = buildSet {
    if (sections.programmes.more) add(SearchSectionName.PROGRAMMES)
    if (sections.channels.more) add(SearchSectionName.CHANNELS)
    if (sections.titles.more) add(SearchSectionName.TITLES)
}

/**
 * The hub's own answer (`discover=false`), which is the page. On an empty view it sets the rows and
 * their order; on a view that already shows this search (a section asked for in full) it only adds
 * rows after the ones shown and keeps the order as it stands.
 */
internal fun SearchView.withLocal(local: SearchResponse, query: String, first: Boolean): SearchView = copy(
    programmes = appendUnseen(programmes, local.programmes) { it.id },
    channels = appendUnseen(channels, local.channels) { it.id },
    titles = appendUnseen(titles, local.titles) { it.id },
    order = if (first) searchOrder(local.order, query, local.channels) else order,
    more = local.moreOf(),
    attribution = local.attribution ?: attribution,
)

/**
 * The full answer, for its titles only: the ones discovery added (those the server names in
 * `discovered`, or, from a server that names none, the ones not shown yet) join the end of Movies &
 * Shows. Titles already on screen keep their places and are not replaced.
 */
internal fun SearchView.withFull(full: SearchResponse): SearchView {
    val have = titles.mapTo(HashSet()) { it.id }
    val named = full.discovered?.toSet()
    val added = full.titles.filter { (named == null || it.id in named) && have.add(it.id) }
    return copy(
        titles = titles + added,
        more = if (full.sections.titles.more) more + SearchSectionName.TITLES else more,
        attribution = full.attribution ?: attribution,
    )
}

/**
 * [loading]: nothing of this search is on screen yet. [discovering]: the hub's rows are shown and the
 * metadata provider's titles are still on their way, so an empty page is not "No results" yet.
 * [growing]: a section's remaining rows are being fetched. [error] only when no answer at all came.
 */
internal data class SearchLoad(
    val request: SearchRequest?,
    val loading: Boolean = false,
    val view: SearchView? = null,
    val discovering: Boolean = false,
    val growing: Boolean = false,
    val error: Throwable? = null,
)

/**
 * A change of scope or query clears the old results at once and debounces the new request; asking
 * for more rows of the same search keeps what is shown. Each request is asked twice: [local] is
 * rendered as soon as it lands, [full] (started alongside) adds its titles when it arrives. An
 * answer to a search that is no longer the current one is dropped with its coroutine.
 */
@OptIn(ExperimentalCoroutinesApi::class)
internal fun searchLoads(
    requests: Flow<SearchRequest?>,
    local: suspend (SearchRequest) -> SearchResponse,
    full: suspend (SearchRequest) -> SearchResponse,
): Flow<SearchLoad> = channelFlow {
    var shown: Pair<SearchRequest, SearchView>? = null
    requests.distinctUntilChanged().collectLatest { request ->
        if (request == null || request.scope.profileId == null || request.scope.server == null || request.query.length < 2) {
            shown = null
            send(SearchLoad(request))
            return@collectLatest
        }
        val kept = shown?.takeIf { request.sameSearch(it.first) }?.second
        if (kept == null) {
            shown = null
            send(SearchLoad(request, loading = true))
            delay(400)
        } else send(SearchLoad(request, view = kept, growing = true))
        coroutineScope {
            val provider = async { attempt { full(request) } }
            val hub = attempt { local(request) }
            var view = kept
            hub.onSuccess { answer ->
                view = (kept ?: SearchView()).withLocal(answer, request.query, first = kept == null)
                shown = request to view!!
                send(SearchLoad(request, view = view, discovering = true))
            }
            val discovered = provider.await()
            discovered.onSuccess { answer ->
                // Without the hub's own answer the full one is all there is: every title of it is shown.
                view = view?.withFull(answer) ?: SearchView().withFull(answer.copy(discovered = null))
                shown = request to view!!
            }
            val error = if (view == null) hub.exceptionOrNull() ?: discovered.exceptionOrNull() else null
            send(SearchLoad(request, view = view, error = error))
        }
    }
}

private suspend fun <T> attempt(block: suspend () -> T): Result<T> =
    try { Result.success(block()) }
    catch (cancelled: CancellationException) { throw cancelled }
    catch (error: Exception) { Result.failure(error) }
