# Catalog response cache

Search and paginated browse cache bounded response snapshots inside the single API worker. No shared HTTP/CDN caching is enabled: responses explicitly use `private, no-store`.

* Search TTL: 15 seconds (includes now/next programme data); browse TTL: 60 seconds.
* LRU limit: 128 entries and 4 MiB serialized payload budget, with a 512 KiB per-entry cap. Oversize values are returned uncached. Expired entries are pruned on access. Concurrent-key bookkeeping is capped at 128 too.
* Keys include normalized whitespace/case query, every endpoint filter/pagination argument, profile ID, profile revision, catalog revision, and public URL origin. Metadata locale currently comes from configured settings/profile preferences, whose changes invalidate these keys; there is no request-locale override in these endpoints.
* Committed ORM and bulk SQL changes to sources (including enabled state and sync upserts), titles, channels, programmes, group rules, service registry or settings invalidate catalog results. Profile preferences, progress, favorites and watchlist invalidate only that profile. Unknown-scope personal bulk mutations conservatively invalidate all profiles. Rollbacks do not invalidate.
* Derived channel-navigation snapshots and metadata/provider offer caches do not invalidate catalog responses: reading them must not invalidate its own request. Their freshness is bounded by the response TTL plus their existing underlying cache policy.
* Same-key loads coalesce. Cancelling one caller preserves other waiters; cancelling the last cancels the load. Errors are not cached. Results are copied before delivery so callers cannot mutate cached state. A revision change during loading retries once; repeated changes return `catalog_changed` rather than exposing the stale response.

This follows the deployment's existing `uvicorn --workers 1` architecture. Writes from another process or manual SQL bypass these process-local revisions and become visible after TTL expiry. Multiple API workers would require a shared committed revision mechanism before promising immediate invalidation. Authentication is still performed on every request; playback decisions are never cached here.

The synthetic 212,021-title regression fixture exercises actual `/search` and CatalogService, with metadata disabled and no provider requests. It reports cold/warm request timing separately from fixture creation and checks response equality/cache reuse. This is a reproducible local query-avoidance measurement, not a production latency guarantee. No database indexes or schema changes are included: leading-wildcard title searches cannot be made selective with an ordinary title B-tree, and adding an index without measured query-plan benefit would add sync cost.
